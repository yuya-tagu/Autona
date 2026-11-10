"""The Socket.IO delta debounce of the web server (ADR-005 S3f-5).

StateManager publishes each save cycle's delta (``state_updated``);
WebServer.on_program_state_update gathers them and sends them to every
client as ``program_state_update``. The schedule and the payloads are an
external contract (Design.md R-Sync-6 / R-Sync-8 / R-Sync-10), pinned here on
a fake clock with no thread:

- a delta that arrives with nothing pending, at least 50 ms after the last
  flush, goes out at once (the leading edge), whatever its size;
- otherwise it opens a window sized by the pending row count at that moment:
  < 10 rows 50 ms, < 100 rows 100 ms, else 200 ms. The quiet spell is
  measured from the last flush, not from the last arrival;
- what arrives while a window is open joins it; the window neither moves
  nor grows;
- the payload is {revision, delta_index, first_revision, delta}, in that key
  order: revision the highest of the batch (delta_index its legacy alias),
  first_revision the first arrival's, delta the rows in arrival order. An
  arrival's revision is its `revision`, else its `delta_index`, else -1;
- nothing is sent for an empty arrival, and a payload the socket cannot
  serialise is replaced by the full state.

The batches go out from one flusher thread (ADR-005 S3f-5; it was one
threading.Timer per batch), pinned in real time with generous margins: it
sends on its own thread and waits out the window; deltas are taken only
between start() and stop(); stop() sends the open batch at once and joins
the thread, giving up on a send that does not return; and a writer -- which
may hold StateManager.lock (agent-pitfalls #17) -- never waits on it.
"""
import copy
import threading
import time
import types

import pytest

from src.AutonaWebServer import WebServer

BASE = 1000.0   # the fake clock's start (s); >= 50 ms after "never flushed"


def _at(ms):
    return BASE + ms / 1000.0


def _rows(rev, n):
    return [{"path": ["score", "1", "team", "1", "rev", str(rev)],
             "action": "values_changed", "value": i} for i in range(n)]


def _changes(rev, n=1):
    """What StateManager.SaveState publishes for one save cycle."""
    return {"revision": rev, "delta_index": rev, "delta": _rows(rev, n)}


def _payload(first, last, rows):
    return {"revision": last, "delta_index": last, "first_revision": first,
            "delta": rows}


class _Clock:
    def __init__(self):
        self.now = BASE

    def __call__(self):
        return self.now


class _FlusherDriver:
    """Takes deltas as start() leaves it, with no thread: run() fires each
    batch at its due time, which is what the flusher thread does.

    (Before S3f-5 the same scenarios ran through a driver that recorded the
    threading.Timer of each batch instead -- green on cd6f5e58.)"""

    def __init__(self, monkeypatch, clock):
        monkeypatch.setattr(WebServer, "_delta_clock", clock)
        monkeypatch.setattr(WebServer, "_delta_due", None)
        monkeypatch.setattr(WebServer, "_delta_running", True)
        monkeypatch.setattr(WebServer, "_delta_thread", None)
        monkeypatch.setattr(WebServer, "_delta_owner", None)
        # A token no flusher holds: one left over from another test, woken
        # by an arrival's notify, returns without touching these batches.
        monkeypatch.setattr(WebServer, "_delta_generation", object())

    def due(self):
        return WebServer._delta_due

    def fire(self):
        WebServer._flush_pending_deltas()


class _Debounce:
    """Feeds arrivals at fake times and fires each batch when it is due."""

    def __init__(self, monkeypatch, record=True):
        self.clock = _Clock()
        self.sent = []
        self.fullStates = 0
        self.fail = None
        monkeypatch.setattr(WebServer, "_delta_pending", [])
        monkeypatch.setattr(WebServer, "_delta_pending_revision", -1)
        monkeypatch.setattr(WebServer, "_delta_pending_first_revision", -1)
        monkeypatch.setattr(WebServer, "_delta_last_flush_ts", 0.0)
        if record:
            monkeypatch.setattr(WebServer, "ws_emit", self._emit)
            monkeypatch.setattr(
                WebServer, "ws_program_state", self._fullState)
        self.driver = _FlusherDriver(monkeypatch, self.clock)

    def _ms(self):
        return round((self.clock.now - BASE) * 1000)

    def _emit(self, event, *args, **kwargs):
        if self.fail is not None:
            raise self.fail
        self.sent.append((self._ms(), event, copy.deepcopy(list(args)),
                          kwargs))

    def _fullState(self, message=None):
        self.fullStates += 1

    def _fireUntil(self, until):
        while self.driver.due() is not None and (
                until is None or self.driver.due() <= until):
            self.clock.now = self.driver.due()
            self.driver.fire()

    def arrive(self, ms, changes):
        self._fireUntil(_at(ms))
        self.clock.now = _at(ms)
        WebServer.on_program_state_update(changes)

    def run(self, steps):
        """steps: (ms, changes). Returns [(ms, payload)] as sent."""
        for ms, changes in steps:
            self.arrive(ms, changes)
        self._fireUntil(None)
        for _ms, event, args, kwargs in self.sent:
            assert event == "program_state_update"
            assert len(args) == 1 and kwargs == {}
        return [(ms, args[0]) for ms, _event, args, _kw in self.sent]

    def armed(self):
        return self.driver.due() is not None


@pytest.fixture
def debounce(monkeypatch):
    return _Debounce(monkeypatch)


# ── the schedule ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("n", [1, 150])
def test_a_delta_after_a_quiet_spell_goes_out_at_once(debounce, n):
    assert debounce.run([(0, _changes(1, n))]) == [
        (0, _payload(1, 1, _rows(1, n)))]


def test_a_burst_waits_out_the_window_measured_from_the_last_flush(debounce):
    sent = debounce.run([
        (0, _changes(1)),        # leading edge
        (10, _changes(2)),       # 10 ms after the flush: a 50 ms window
        (20, _changes(3, 2)),
        (59, _changes(4)),
        # 100 is 90 ms after the last arrival but 40 ms after the flush at
        # 60: a window again.
        (100, _changes(5)),
        (300, _changes(6)),      # quiet again: at once
    ])
    assert sent == [
        (0, _payload(1, 1, _rows(1, 1))),
        (60, _payload(2, 4, _rows(2, 1) + _rows(3, 2) + _rows(4, 1))),
        (150, _payload(5, 5, _rows(5, 1))),
        (300, _payload(6, 6, _rows(6, 1))),
    ]


@pytest.mark.parametrize("gap, sentAt", [(49, 99), (51, 51)])
def test_the_quiet_spell_is_50_ms(debounce, gap, sentAt):
    sent = debounce.run([(0, _changes(1)), (gap, _changes(2))])
    assert sent == [(0, _payload(1, 1, _rows(1, 1))),
                    (sentAt, _payload(2, 2, _rows(2, 1)))]


@pytest.mark.parametrize("n, window", [
    (1, 50), (9, 50), (10, 100), (99, 100), (100, 200), (250, 200)])
def test_the_window_is_sized_by_the_rows_pending_when_it_opens(
        debounce, n, window):
    sent = debounce.run([(0, _changes(1)), (10, _changes(2, n))])
    assert sent == [(0, _payload(1, 1, _rows(1, 1))),
                    (10 + window, _payload(2, 2, _rows(2, n)))]


def test_the_window_neither_moves_nor_grows(debounce):
    sent = debounce.run([
        (0, _changes(1)),
        (10, _changes(2)),         # a 50 ms window, due at 60
        (20, _changes(3, 150)),    # would size a 200 ms one: joins instead
        (55, _changes(4)),
    ])
    assert sent == [
        (0, _payload(1, 1, _rows(1, 1))),
        (60, _payload(2, 4, _rows(2, 1) + _rows(3, 150) + _rows(4, 1))),
    ]


# ── the payload ──────────────────────────────────────────────────────────

def test_the_payload_spans_the_batch_and_keeps_its_key_order(debounce):
    sent = debounce.run([
        (0, _changes(1)),
        # Out of order on purpose: revision is the highest, first_revision
        # the first arrival's.
        (10, _changes(5)),
        (20, _changes(4)),
        (30, _changes(7)),
        (40, _changes(6)),
    ])
    assert sent == [
        (0, _payload(1, 1, _rows(1, 1))),
        (60, _payload(5, 7, _rows(5, 1) + _rows(4, 1) + _rows(7, 1)
                      + _rows(6, 1))),
    ]
    for _ms, payload in sent:
        assert list(payload) == [
            "revision", "delta_index", "first_revision", "delta"]


@pytest.mark.parametrize("changes, revision", [
    ({"revision": 3, "delta_index": 2, "delta": _rows(3, 1)}, 3),
    ({"delta_index": 9, "delta": _rows(3, 1)}, 9),
    ({"delta": _rows(3, 1)}, -1),
])
def test_the_revision_falls_back_to_delta_index_then_minus_one(
        debounce, changes, revision):
    assert debounce.run([(0, changes)]) == [
        (0, _payload(revision, revision, _rows(3, 1)))]


@pytest.mark.parametrize("changes", [None, {}])
def test_no_changes_sends_and_arms_nothing(debounce, changes):
    debounce.arrive(0, changes)
    assert not debounce.armed()
    assert debounce.run([]) == []


@pytest.mark.parametrize("delta", [[], None])
def test_an_empty_delta_sends_nothing_and_is_not_a_flush(debounce, delta):
    sent = debounce.run([
        (0, {"revision": 3, "delta_index": 3, "delta": delta}),
        (10, _changes(4)),   # still quiet: the empty one flushed nothing
    ])
    assert sent == [(10, _payload(4, 4, _rows(4, 1)))]


def test_a_payload_the_socket_cannot_serialise_sends_the_full_state(
        debounce):
    debounce.fail = TypeError("not serialisable")
    debounce.arrive(0, _changes(1))
    debounce.run([])
    assert debounce.fullStates == 1
    # The batch is gone either way: the next one starts afresh.
    debounce.fail = None
    assert debounce.run([(100, _changes(2))]) == [
        (100, _payload(2, 2, _rows(2, 1)))]


def test_any_other_send_failure_drops_the_batch(debounce):
    debounce.fail = RuntimeError("socket gone")
    debounce.arrive(0, _changes(1))
    debounce.run([])
    assert debounce.fullStates == 0
    debounce.fail = None
    assert debounce.run([(100, _changes(2))]) == [
        (100, _payload(2, 2, _rows(2, 1)))]


# ── a send that follows the deltas (ADR-005 S3f-4 review) ────────────────
# The full state of a game change carries the load's revision: sent ahead
# of that revision's delta, still waiting in the open batch, it reaches the
# clients first. _after_pending_deltas makes the batch due now and runs the
# send right after it, from the one sender.


def test_a_send_waiting_for_the_batch_makes_it_due_now(debounce, monkeypatch):
    monkeypatch.setattr(WebServer, "_delta_after", [])
    debounce.arrive(0, _changes(1))     # the leading edge
    debounce.arrive(10, _changes(2))    # a window, due at 60
    debounce.clock.now = _at(20)
    WebServer._after_pending_deltas(
        lambda: WebServer.ws_emit("program_state", "the full state"))

    assert debounce.driver.due() == _at(20)
    debounce._fireUntil(None)
    assert [(ms, event, args) for ms, event, args, _kw in debounce.sent] == [
        (0, "program_state_update", [_payload(1, 1, _rows(1, 1))]),
        (20, "program_state_update", [_payload(2, 2, _rows(2, 1))]),
        (20, "program_state", ["the full state"]),
    ]
    assert WebServer._delta_after == []


def test_a_send_waiting_for_no_batch_goes_out_at_once(debounce, monkeypatch):
    monkeypatch.setattr(WebServer, "_delta_after", [])
    WebServer._after_pending_deltas(
        lambda: WebServer.ws_emit("program_state", "the full state"))
    debounce._fireUntil(None)
    assert [(ms, event) for ms, event, _args, _kw in debounce.sent] == [
        (0, "program_state")]


def test_without_a_flusher_a_waiting_send_runs_here(debounce, monkeypatch):
    monkeypatch.setattr(WebServer, "_delta_after", [])
    monkeypatch.setattr(WebServer, "_delta_running", False)
    ran = []
    WebServer._after_pending_deltas(lambda: ran.append(1))
    assert ran == [1] and WebServer._delta_after == []


# ── on the wire ──────────────────────────────────────────────────────────

@pytest.fixture
def wire(monkeypatch, tmp_path):
    """The real Socket.IO server with a test client connected from this PC
    (the same setup as tests/test_web_operations.py's `server`)."""
    from src import AutonaWebOperations as WebOps
    from src.AutonaWebServer import _build_web_app
    from src.core import WebAccess
    from src.core.SettingsManager import SettingsManager

    monkeypatch.setattr(SettingsManager, "settings", {})
    monkeypatch.setattr(SettingsManager, "SaveSettings", lambda: None)
    monkeypatch.setattr(SettingsManager, "loadFailed", False)
    monkeypatch.setattr(WebAccess, "_token", None)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(WebOps, "openSetSelection", None)
    monkeypatch.setattr(WebOps, "renderThumbnail", None)
    monkeypatch.setattr(WebOps, "startImageRender", None)
    monkeypatch.setattr(WebServer, "_lan_hosts", ())
    monkeypatch.setattr(WebServer, "_refusals", WebAccess.RefusalLog())

    app, socketio = _build_web_app()
    monkeypatch.setattr(WebServer, "socketio", socketio)
    flask_client = app.test_client()
    flask_client.environ_base["REMOTE_ADDR"] = "127.0.0.1"
    ws = socketio.test_client(app, flask_test_client=flask_client)
    ws.get_received()   # the program_state sent on connect
    yield ws
    ws.disconnect()


def test_the_batches_reach_a_client_unchanged_and_in_order(
        monkeypatch, wire):
    # The real send: WebServer.ws_emit through the Socket.IO server.
    debounce = _Debounce(monkeypatch, record=False)
    for ms, changes in [(0, _changes(1)), (10, _changes(2)),
                        (20, _changes(3, 2))]:
        debounce.arrive(ms, changes)
    debounce._fireUntil(None)
    received = wire.get_received()
    assert [(packet["name"], packet["namespace"]) for packet in received] == [
        ("program_state_update", "/"), ("program_state_update", "/")]
    assert [packet["args"] for packet in received] == [
        [_payload(1, 1, _rows(1, 1))],
        [_payload(2, 3, _rows(2, 1) + _rows(3, 2))],
    ]
    for packet in received:
        assert list(packet["args"][0]) == [
            "revision", "delta_index", "first_revision", "delta"]


# ── the flusher thread ───────────────────────────────────────────────────

FLUSHER = "AutonaWebServerDeltas"


class _Sends:
    """WebServer.ws_emit, recording each send, the thread it ran on and
    when (on perf_counter: a 50 ms window is about three steps of
    time.monotonic on Windows)."""

    def __init__(self):
        self.cond = threading.Condition()
        self.sent = []
        self.during = None   # run inside each send, before it is recorded

    def emit(self, event, *args, **kwargs):
        if self.during is not None:
            self.during()
        with self.cond:
            self.sent.append((threading.current_thread(), time.perf_counter(),
                              event, copy.deepcopy(args[0])))
            self.cond.notify_all()

    def waitFor(self, n, timeout=5.0):
        with self.cond:
            return self.cond.wait_for(lambda: len(self.sent) >= n, timeout)

    def payloads(self):
        with self.cond:
            return [payload for _t, _at, _e, payload in self.sent]

    def threads(self):
        with self.cond:
            return [thread for thread, _at, _e, _p in self.sent]


class _FrozenClock:
    """The schedule's clock, stopped: every arrival after the first flush
    is inside the quiet spell, whatever the machine's speed. The flusher
    still waits the window out in real time (due - now = the window)."""

    def __call__(self):
        return BASE


def _stuckOnce(entered, release):
    """A send hook that holds the first send until `release` is set and
    lets the later ones through."""
    calls = []

    def during():
        calls.append(None)
        if len(calls) == 1:
            entered.set()
            release.wait(10)
    return during


@pytest.fixture
def flusher(monkeypatch):
    sends = _Sends()
    monkeypatch.setattr(WebServer, "_delta_pending", [])
    monkeypatch.setattr(WebServer, "_delta_pending_revision", -1)
    monkeypatch.setattr(WebServer, "_delta_pending_first_revision", -1)
    monkeypatch.setattr(WebServer, "_delta_last_flush_ts", 0.0)
    monkeypatch.setattr(WebServer, "_delta_due", None)
    monkeypatch.setattr(WebServer, "_delta_after", [])
    monkeypatch.setattr(WebServer, "_delta_running", False)
    monkeypatch.setattr(WebServer, "_delta_thread", None)
    monkeypatch.setattr(WebServer, "_delta_owner", None)
    monkeypatch.setattr(WebServer, "_delta_generation", None)
    monkeypatch.setattr(WebServer, "ws_emit", sends.emit)
    monkeypatch.setattr(WebServer, "ws_program_state",
                        lambda message=None: None)
    owner = types.SimpleNamespace(name="the server")
    sends.start = lambda: WebServer._start_delta_flusher(owner)
    sends.stop = lambda timeout=5.0: WebServer._stop_delta_flusher(
        owner, timeout)
    yield sends
    sends.during = None
    # Whichever server's flusher is still up (a test that failed half way).
    if WebServer._delta_thread is not None:
        WebServer._stop_delta_flusher(WebServer._delta_owner, 5.0)


def test_the_flusher_runs_from_start_to_stop_only(flusher):
    assert WebServer._delta_thread is None

    # Before start(): nobody to send to -- not even queued.
    WebServer.on_program_state_update(_changes(1))
    assert (WebServer._delta_pending, WebServer._delta_due) == ([], None)

    flusher.start()
    thread = WebServer._delta_thread
    assert (thread.name, thread.daemon, thread.is_alive()) == (
        FLUSHER, True, True)
    flusher.start()                                   # once is enough
    WebServer._start_delta_flusher(types.SimpleNamespace())  # another server
    assert WebServer._delta_thread is thread
    # Only the server that started it stops it.
    assert WebServer._stop_delta_flusher(types.SimpleNamespace(), 5.0)
    assert thread.is_alive()

    assert flusher.stop() is True
    assert not thread.is_alive() and WebServer._delta_thread is None
    assert flusher.stop() is True                     # again: nothing to do

    # After stop(): no thread to send them.
    WebServer.on_program_state_update(_changes(2))
    assert (WebServer._delta_pending, WebServer._delta_due) == ([], None)
    assert flusher.payloads() == []


def test_a_send_waiting_for_the_open_batch_follows_it_from_the_flusher(
        flusher, monkeypatch):
    """The open batch (a window from the frozen clock) goes out without
    waiting the window out, and the waiting send right after it, on the
    flusher's thread."""
    monkeypatch.setattr(WebServer, "_delta_clock", _FrozenClock())
    flusher.start()
    thread = WebServer._delta_thread
    WebServer.on_program_state_update(_changes(1))      # the leading edge
    assert flusher.waitFor(1)
    WebServer.on_program_state_update(_changes(2, 150))  # a 200 ms window
    ran = []

    def fullState():
        ran.append(threading.current_thread())
        WebServer.ws_emit("program_state", "the full state")

    WebServer._after_pending_deltas(fullState)
    assert flusher.waitFor(3)

    assert [event for _t, _at, event, _p in flusher.sent] == [
        "program_state_update", "program_state_update", "program_state"]
    assert flusher.payloads()[1] == _payload(2, 2, _rows(2, 150))
    assert ran == [thread] and flusher.threads()[2] is thread


def test_a_quiet_delta_goes_out_at_once_from_the_flusher(flusher):
    flusher.start()
    thread = WebServer._delta_thread
    WebServer.on_program_state_update(_changes(1))
    assert flusher.waitFor(1)
    assert flusher.threads() == [thread]
    assert flusher.sent[0][2] == "program_state_update"
    assert flusher.payloads() == [_payload(1, 1, _rows(1, 1))]


@pytest.mark.parametrize("n, window, joiner", [
    (1, 0.05, False),
    # Long enough for a second arrival to join it for sure: it does not
    # wake the flusher early.
    (100, 0.2, True),
])
def test_the_flusher_waits_out_the_window(
        flusher, monkeypatch, n, window, joiner):
    monkeypatch.setattr(WebServer, "_delta_clock", _FrozenClock())
    flusher.start()
    thread = WebServer._delta_thread
    WebServer.on_program_state_update(_changes(1))
    assert flusher.waitFor(1)

    opened = time.perf_counter()
    WebServer.on_program_state_update(_changes(2, n))     # opens the window
    rows = _rows(2, n)
    if joiner:
        WebServer.on_program_state_update(_changes(3))
        rows += _rows(3, 1)
    assert flusher.waitFor(2)

    # The flusher waits `window` from after `opened`, and CPython's timed
    # lock wait does not return early: the margin is for the clocks only.
    waited = flusher.sent[1][1] - opened
    assert window * 0.8 <= waited < 5.0, waited
    assert flusher.payloads() == [
        _payload(1, 1, _rows(1, 1)),
        _payload(2, 3 if joiner else 2, rows),
    ]
    assert flusher.threads() == [thread, thread]


def test_stop_sends_the_open_batch_at_once(flusher, monkeypatch):
    monkeypatch.setattr(WebServer, "_delta_clock", _FrozenClock())
    monkeypatch.setattr(WebServer, "_adaptive_debounce_sec",
                        staticmethod(lambda count: 30.0))
    flusher.start()
    thread = WebServer._delta_thread
    WebServer.on_program_state_update(_changes(1))
    assert flusher.waitFor(1)
    WebServer.on_program_state_update(_changes(2))        # a 30 s window

    started = time.monotonic()
    assert flusher.stop(10.0) is True
    assert time.monotonic() - started < 5.0
    assert flusher.payloads() == [_payload(1, 1, _rows(1, 1)),
                                  _payload(2, 2, _rows(2, 1))]
    assert flusher.threads() == [thread, thread]


def test_stop_gives_up_on_a_send_that_does_not_return(flusher, monkeypatch):
    monkeypatch.setattr(WebServer, "_delta_clock", _FrozenClock())
    monkeypatch.setattr(WebServer, "_adaptive_debounce_sec",
                        staticmethod(lambda count: 30.0))
    entered, release = threading.Event(), threading.Event()
    flusher.during = _stuckOnce(entered, release)
    flusher.start()
    thread = WebServer._delta_thread
    WebServer.on_program_state_update(_changes(1))
    assert entered.wait(5)
    WebServer.on_program_state_update(_changes(2))        # a 30 s window

    started = time.monotonic()
    assert flusher.stop(0.3) is False
    assert time.monotonic() - started < 3.0
    # Let go: a later start() may make a new flusher.
    assert WebServer._delta_thread is None

    # No start() since, so it still owns the queue: once its send returns,
    # the batch left open goes out as its last.
    release.set()
    thread.join(5)
    assert not thread.is_alive()
    assert flusher.payloads() == [_payload(1, 1, _rows(1, 1)),
                                  _payload(2, 2, _rows(2, 1))]
    assert flusher.threads() == [thread, thread]


def test_a_flusher_stop_gave_up_on_leaves_the_next_ones_batches_alone(
        flusher, monkeypatch):
    monkeypatch.setattr(WebServer, "_delta_clock", _FrozenClock())
    monkeypatch.setattr(WebServer, "_adaptive_debounce_sec",
                        staticmethod(lambda count: 30.0))
    entered, release = threading.Event(), threading.Event()
    flusher.during = _stuckOnce(entered, release)
    flusher.start()
    abandoned = WebServer._delta_thread
    WebServer.on_program_state_update(_changes(1))
    assert entered.wait(5)
    WebServer.on_program_state_update(_changes(2))        # left open
    assert flusher.stop(0.3) is False

    nextServer = types.SimpleNamespace(name="the next server")
    WebServer._start_delta_flusher(nextServer)
    current = WebServer._delta_thread
    assert current is not abandoned
    # The batch left open is dropped, like any delta from before start().
    assert (WebServer._delta_pending, WebServer._delta_due) == ([], None)
    WebServer.on_program_state_update(_changes(3))        # a 30 s window

    release.set()
    abandoned.join(5)
    assert not abandoned.is_alive()
    # It returned without taking the new flusher's batch.
    assert flusher.payloads() == [_payload(1, 1, _rows(1, 1))]
    assert WebServer._delta_pending == _rows(3, 1)
    assert WebServer._delta_due is not None

    assert WebServer._stop_delta_flusher(nextServer, 5.0) is True
    assert flusher.payloads() == [_payload(1, 1, _rows(1, 1)),
                                  _payload(3, 3, _rows(3, 1))]
    assert flusher.threads() == [abandoned, current]


def test_what_waited_for_a_batch_a_stuck_flusher_left_runs_after_a_restart(
        flusher, monkeypatch):
    """A send waiting for the open batch (a game change's full state) when
    stop() gave up on a flusher stuck in a send: the next start() drops the
    batch, like any delta from before it, but what waited for it is not
    dropped -- the new flusher runs it at once (ADR-005 S3f-4 review)."""
    monkeypatch.setattr(WebServer, "_delta_clock", _FrozenClock())
    monkeypatch.setattr(WebServer, "_adaptive_debounce_sec",
                        staticmethod(lambda count: 30.0))
    entered, release = threading.Event(), threading.Event()
    flusher.during = _stuckOnce(entered, release)
    flusher.start()
    abandoned = WebServer._delta_thread
    WebServer.on_program_state_update(_changes(1))
    assert entered.wait(5)
    WebServer.on_program_state_update(_changes(2))        # left open
    ran = []
    sent = threading.Event()

    def push():
        ran.append(threading.current_thread())
        sent.set()

    WebServer._after_pending_deltas(push)
    assert flusher.stop(0.3) is False
    assert ran == []

    nextServer = types.SimpleNamespace(name="the next server")
    WebServer._start_delta_flusher(nextServer)
    current = WebServer._delta_thread
    try:
        assert sent.wait(5)
        assert ran == [current]
        assert WebServer._delta_after == [] and WebServer._delta_pending == []
    finally:
        release.set()
        abandoned.join(5)
        assert WebServer._stop_delta_flusher(nextServer, 5.0) is True
    # The batch left open was dropped; the abandoned flusher took nothing.
    assert flusher.payloads() == [_payload(1, 1, _rows(1, 1))]


def test_a_writer_holding_the_state_lock_never_waits_on_the_flusher(
        flusher):
    # agent-pitfalls #17: on_program_state_update runs under
    # StateManager.lock when the write was on the GUI thread. Here the
    # flusher's send needs that lock (as its full-state fallback does): the
    # writer must still get through while the flusher is stuck behind it.
    from src.core.StateManager import StateManager

    entered = threading.Event()

    def needsTheStateLock():
        entered.set()
        with StateManager.lock:
            pass

    flusher.during = needsTheStateLock
    flusher.start()
    done = threading.Event()

    def writer():
        with StateManager.lock:
            WebServer.on_program_state_update(_changes(1))
            if entered.wait(5):
                WebServer.on_program_state_update(_changes(2))
                done.set()

    thread = threading.Thread(target=writer, daemon=True)
    thread.start()
    thread.join(10)
    assert done.is_set() and not thread.is_alive()

    assert flusher.waitFor(2)
    assert flusher.payloads() == [_payload(1, 1, _rows(1, 1)),
                                  _payload(2, 2, _rows(2, 1))]


# ── the pending values' notice (ADR-005 S4u) ─────────────────────────────
# held_state_updated / held_dirty_changed of a board become one
# `scoreboard_pending` {board, dirty}: no value (the client reads the guarded
# GET), `dirty` read when it is sent, after the deltas of its flush, from the
# flusher, on a schedule of its own.

PENDING = "scoreboard_pending"


@pytest.fixture
def notices(monkeypatch, debounce):
    monkeypatch.setattr(WebServer, "_held_notices", set())
    monkeypatch.setattr(WebServer, "_held_notice_ts", 0.0)
    monkeypatch.setattr(WebServer, "_ws_trusted_rooms",
                        staticmethod(lambda: ["the trusted room"]))
    from src.core.StateManager import StateManager
    debounce.dirty = {}
    monkeypatch.setattr(
        StateManager, "PeekPendingChanges",
        staticmethod(lambda prefix: debounce.dirty.get(prefix, False)))
    return debounce


def _toldAt(debounce, ms, prefix="score.1", *dirty):
    debounce._fireUntil(_at(ms))
    debounce.clock.now = _at(ms)
    WebServer.on_held_change(prefix, *dirty)


def _notice(ms, board, dirty):
    return (ms, PENDING, [{"board": board, "dirty": dirty}],
            {"to": ["the trusted room"]})


def test_a_notice_after_a_quiet_spell_goes_out_at_once(notices):
    _toldAt(notices, 0)
    notices._fireUntil(None)
    assert notices.sent == [_notice(0, 1, False)]


@pytest.mark.parametrize("told", [(), (True,), (False,)])
def test_both_channels_say_the_same_thing(notices, told):
    # held_state_updated(prefix) and held_dirty_changed(prefix, dirty): what
    # the notice says is read when it is sent, not what the channel said.
    notices.dirty["score.2"] = True
    _toldAt(notices, 0, "score.2", *told)
    notices._fireUntil(None)
    assert notices.sent == [_notice(0, 2, True)]


def test_a_burst_is_one_notice_a_window(notices):
    _toldAt(notices, 0)
    _toldAt(notices, 10)
    _toldAt(notices, 20)
    _toldAt(notices, 30, "score.1", True)
    _toldAt(notices, 59)
    _toldAt(notices, 200)
    notices._fireUntil(None)
    assert notices.sent == [_notice(0, 1, False), _notice(60, 1, False),
                            _notice(200, 1, False)]


def test_the_flag_is_the_one_when_it_is_sent(notices):
    # The True of a load's first write, then the False of its commit, inside
    # one window: the client is told once, and what is true by then.
    _toldAt(notices, 0)
    notices._fireUntil(_at(0))
    notices.dirty["score.1"] = True
    _toldAt(notices, 10, "score.1", True)
    notices.dirty["score.1"] = False
    _toldAt(notices, 20, "score.1", False)
    notices._fireUntil(None)
    assert notices.sent == [_notice(0, 1, False), _notice(60, 1, False)]


def test_each_board_has_its_notice(notices):
    _toldAt(notices, 0, "score.2")
    WebServer.on_held_change("score.1")
    notices.dirty["score.1"] = True
    notices._fireUntil(None)
    assert notices.sent == [_notice(0, 1, True), _notice(0, 2, False)]


@pytest.mark.parametrize("prefix", [
    "score", "score.", "score.x", "score.1.team", "top8.1", "commentary"])
def test_only_a_board_is_told_about(notices, prefix):
    WebServer.on_held_change(prefix)
    WebServer.on_held_change(prefix, True)
    assert WebServer._held_notices == set()
    assert notices.driver.due() is None


def test_a_notice_goes_after_the_deltas_of_its_flush(notices):
    notices.arrive(0, _changes(1))
    notices.arrive(10, _changes(2))
    _toldAt(notices, 20)
    notices.arrive(30, _changes(3))
    notices._fireUntil(None)

    assert [(ms, event) for ms, event, _a, _k in notices.sent] == [
        (0, "program_state_update"), (60, "program_state_update"),
        (60, PENDING)]
    # The rows of the delta are the ones they would have been.
    assert notices.sent[1][2] == [
        _payload(2, 3, _rows(2, 1) + _rows(3, 1))]


def test_the_deltas_schedule_does_not_follow_a_notice(notices):
    # A flush of notices alone is no flush of deltas, so the delta right after
    # one is on its own leading edge.
    _toldAt(notices, 0)
    notices.arrive(10, _changes(1))
    notices._fireUntil(None)
    assert [(ms, event) for ms, event, _a, _k in notices.sent] == [
        (0, PENDING), (10, "program_state_update")]


def test_a_notice_does_not_follow_the_deltas_schedule(notices):
    notices.arrive(0, _changes(1))
    _toldAt(notices, 10)
    notices._fireUntil(None)
    assert [(ms, event) for ms, event, _a, _k in notices.sent] == [
        (0, "program_state_update"), (10, PENDING)]


def test_a_delta_joins_a_notices_open_window(notices):
    # The one thing the two schedules share (the batch): up to 50 ms on a
    # delta that had a leading edge coming. Documented in the docs of S4u.
    _toldAt(notices, 0)
    _toldAt(notices, 10)
    notices.arrive(20, _changes(1))
    notices._fireUntil(None)
    assert [(ms, event) for ms, event, _a, _k in notices.sent] == [
        (0, PENDING), (60, "program_state_update"), (60, PENDING)]


def test_a_delta_can_leave_ahead_of_its_own_window_with_a_notices(notices):
    # The other side of it: on its own, this delta (20 ms after a flush) would
    # wait until 70 ms. Documented in the docs of S4u too.
    notices.arrive(0, _changes(1))
    _toldAt(notices, 5)
    _toldAt(notices, 10)
    notices.arrive(20, _changes(2))
    notices._fireUntil(None)
    assert [(ms, event) for ms, event, _a, _k in notices.sent] == [
        (0, "program_state_update"), (5, PENDING),
        (60, "program_state_update"), (60, PENDING)]


def test_a_failed_notice_takes_nothing_with_it(notices):
    notices.fail = RuntimeError("the socket said no")
    _toldAt(notices, 0, "score.1")
    WebServer.on_held_change("score.2")
    notices._fireUntil(None)
    notices.fail = None
    _toldAt(notices, 100)
    notices._fireUntil(None)
    assert notices.sent == [_notice(100, 1, False)]


def test_without_a_flusher_a_notice_is_not_kept(notices, monkeypatch):
    monkeypatch.setattr(WebServer, "_delta_running", False)
    WebServer.on_held_change("score.1")
    assert WebServer._held_notices == set()
    assert notices.driver.due() is None


def _pendingOnly(sends):
    with sends.cond:
        return [(event, payload) for _t, _at, event, payload in sends.sent
                if event == PENDING]


def test_the_flusher_sends_a_notice_on_its_own_thread(flusher):
    flusher.start()
    thread = WebServer._delta_thread

    WebServer.on_held_change("score.1")

    assert flusher.waitFor(1)
    assert _pendingOnly(flusher) == [(PENDING, {"board": 1, "dirty": False})]
    assert flusher.threads() == [thread]
    assert flusher.stop() is True


def test_stop_sends_the_notices_still_open(flusher, monkeypatch):
    monkeypatch.setattr(WebServer, "_delta_clock", _FrozenClock())
    flusher.start()
    WebServer.on_held_change("score.1")    # at once
    assert flusher.waitFor(1)

    WebServer.on_held_change("score.2")    # a 50 ms window, on a stopped clock
    assert flusher.stop() is True

    assert _pendingOnly(flusher) == [
        (PENDING, {"board": 1, "dirty": False}),
        (PENDING, {"board": 2, "dirty": False})]

    # After stop(): nothing is kept, and a new start() starts clean.
    WebServer.on_held_change("score.1")
    assert WebServer._held_notices == set()


def test_a_notice_goes_out_with_the_state_lock_held(flusher, monkeypatch):
    # agent-pitfalls #17: both channels run under StateManager.lock when the
    # write was on the GUI thread, and a save cycle can keep it a while. This
    # flusher sends every client's deltas, so it peeks at the flag instead of
    # stopping behind the lock: each notice arrives with the writer still
    # holding it. (Reading under the lock, the first wait here runs out.)
    from src.core.StateManager import StateManager

    monkeypatch.setattr(StateManager, "heldKeys", {})
    flusher.start()
    arrived = []

    def writer():
        with StateManager.lock:
            WebServer.on_held_change("score.1")
            arrived.append(flusher.waitFor(1, timeout=30))
            StateManager.heldKeys["score.1"] = ["a key"]
            WebServer.on_held_change("score.1", True)
            arrived.append(flusher.waitFor(2, timeout=30))

    thread = threading.Thread(target=writer, daemon=True)
    thread.start()
    thread.join(90)
    assert not thread.is_alive()

    assert arrived == [True, True]
    assert _pendingOnly(flusher) == [
        (PENDING, {"board": 1, "dirty": False}),
        (PENDING, {"board": 1, "dirty": True})]


# ── what a board follows' notice (ADR-005 S4v) ───────────────────────────
# SetFollowService.changes of a board become one `scoreboard_follow` {board}:
# no value (the client reads GET /scoreboards/{n}/follow), to everyone (that GET
# is a READ), after the deltas of its flush, from the flusher.

FOLLOW = "scoreboard_follow"


@pytest.fixture
def follows(monkeypatch, debounce):
    monkeypatch.setattr(WebServer, "_follow_notices", set())
    return debounce


def _followedAt(debounce, ms, board=1):
    debounce._fireUntil(_at(ms))
    debounce.clock.now = _at(ms)
    WebServer.on_follow_change(board)


def test_a_follow_notice_goes_out_at_once_to_everyone(follows):
    _followedAt(follows, 0)
    follows._fireUntil(None)
    assert follows.sent == [(0, FOLLOW, [{"board": 1}], {})]


def test_a_flush_tells_each_board_once(follows):
    _followedAt(follows, 0, 2)
    WebServer.on_follow_change(1)
    WebServer.on_follow_change(2)
    follows._fireUntil(None)
    assert follows.sent == [(0, FOLLOW, [{"board": 1}], {}),
                            (0, FOLLOW, [{"board": 2}], {})]


def test_a_follow_notice_goes_after_the_deltas_of_its_flush(follows):
    follows.arrive(0, _changes(1))
    follows.arrive(10, _changes(2))
    _followedAt(follows, 20)
    follows.arrive(30, _changes(3))
    follows._fireUntil(None)

    assert [(ms, event) for ms, event, _a, _k in follows.sent] == [
        (0, "program_state_update"), (60, "program_state_update"), (60, FOLLOW)]
    assert follows.sent[1][2] == [_payload(2, 3, _rows(2, 1) + _rows(3, 1))]


def test_the_deltas_schedule_does_not_follow_a_follow_notice(follows):
    _followedAt(follows, 0)
    follows.arrive(10, _changes(1))
    follows._fireUntil(None)
    assert [(ms, event) for ms, event, _a, _k in follows.sent] == [
        (0, FOLLOW), (10, "program_state_update")]


def test_a_failed_follow_notice_takes_nothing_with_it(follows):
    follows.fail = RuntimeError("the socket said no")
    _followedAt(follows, 0, 1)
    WebServer.on_follow_change(2)
    follows._fireUntil(None)
    follows.fail = None
    _followedAt(follows, 100)
    follows._fireUntil(None)
    assert follows.sent == [(100, FOLLOW, [{"board": 1}], {})]


def test_without_a_flusher_a_follow_notice_is_not_kept(follows, monkeypatch):
    monkeypatch.setattr(WebServer, "_delta_running", False)
    WebServer.on_follow_change(1)
    assert WebServer._follow_notices == set()
    assert follows.driver.due() is None


def _followOnly(sends):
    with sends.cond:
        return [(event, payload) for _t, _at, event, payload in sends.sent
                if event == FOLLOW]


def test_the_flusher_sends_a_follow_notice_on_its_own_thread(flusher):
    flusher.start()
    thread = WebServer._delta_thread

    WebServer.on_follow_change(1)

    assert flusher.waitFor(1)
    assert _followOnly(flusher) == [(FOLLOW, {"board": 1})]
    assert flusher.threads() == [thread]
    assert flusher.stop() is True


def test_a_follow_notice_follows_the_deltas_from_the_flusher(
        flusher, monkeypatch):
    """The flusher's own take and send, not the test driver's: a page that
    reads the board after the notice has the deltas of that flush by then."""
    monkeypatch.setattr(WebServer, "_delta_clock", _FrozenClock())
    flusher.start()
    WebServer.on_program_state_update(_changes(1))      # the leading edge
    assert flusher.waitFor(1)
    WebServer.on_program_state_update(_changes(2, 150))  # a 200 ms window
    WebServer.on_follow_change(1)                        # in it
    assert flusher.stop() is True

    assert [event for _t, _at, event, _p in flusher.sent] == [
        "program_state_update", "program_state_update", FOLLOW]


def test_stop_sends_the_follow_notices_still_open(flusher, monkeypatch):
    monkeypatch.setattr(WebServer, "_delta_clock", _FrozenClock())
    flusher.start()
    WebServer.on_held_change("score.1")    # at once
    assert flusher.waitFor(1)

    WebServer.on_held_change("score.2")    # a 50 ms window, on a stopped clock
    WebServer.on_follow_change(1)          # in it
    assert flusher.stop() is True

    assert _followOnly(flusher) == [(FOLLOW, {"board": 1})]

    # After stop(): nothing is kept, and a new start() starts clean.
    WebServer.on_follow_change(1)
    assert WebServer._follow_notices == set()


def test_a_new_start_starts_with_no_follow_notice(flusher, monkeypatch):
    flusher.start()
    assert flusher.stop() is True
    monkeypatch.setattr(WebServer, "_follow_notices", {2})

    flusher.start()
    WebServer.on_held_change("score.1")
    assert flusher.waitFor(1)
    assert flusher.stop() is True
    assert _followOnly(flusher) == []


def test_a_follow_notice_goes_out_with_the_state_lock_held(flusher):
    # agent-pitfalls #17: told where the board's operations run, which may hold
    # StateManager.lock. Only queued there: it is the flusher that sends it.
    from src.core.StateManager import StateManager

    flusher.start()
    arrived = []

    def writer():
        with StateManager.lock:
            WebServer.on_follow_change(1)
            arrived.append(flusher.waitFor(1, timeout=30))

    thread = threading.Thread(target=writer, daemon=True)
    thread.start()
    thread.join(60)
    assert not thread.is_alive()
    assert arrived == [True]
    assert flusher.stop() is True


# ── the battle format's notice (ADR-005 S4z) ─────────────────────────────
# AutonaActions.battleFormatChanges becomes one `battle_format` {}: no value
# (the client reads GET /battle-format), to everyone (that GET is a READ),
# after the deltas and the other notices of its flush, from the flusher.

FORMAT = "battle_format"


@pytest.fixture
def formats(monkeypatch, debounce):
    monkeypatch.setattr(WebServer, "_follow_notices", set())
    monkeypatch.setattr(WebServer, "_battle_format_notice", False)
    return debounce


def _switchedAt(debounce, ms, fmt="team"):
    debounce._fireUntil(_at(ms))
    debounce.clock.now = _at(ms)
    WebServer.on_battle_format_change(fmt)


def test_a_battle_format_notice_goes_out_at_once_to_everyone(formats):
    _switchedAt(formats, 0)
    formats._fireUntil(None)
    assert formats.sent == [(0, FORMAT, [{}], {})]


def test_a_flush_tells_the_format_once(formats):
    _switchedAt(formats, 0, "team")
    WebServer.on_battle_format_change("crew")
    WebServer.on_battle_format_change("singles")
    formats._fireUntil(None)
    assert formats.sent == [(0, FORMAT, [{}], {})]


def test_a_battle_format_notice_goes_after_the_deltas_and_the_follow(formats):
    formats.arrive(0, _changes(1))
    formats.arrive(10, _changes(2))
    _switchedAt(formats, 20)
    WebServer.on_follow_change(1)
    formats.arrive(30, _changes(3))
    formats._fireUntil(None)

    assert [(ms, event) for ms, event, _a, _k in formats.sent] == [
        (0, "program_state_update"), (60, "program_state_update"),
        (60, FOLLOW), (60, FORMAT)]


def test_the_deltas_schedule_does_not_follow_a_format_notice(formats):
    _switchedAt(formats, 0)
    formats.arrive(10, _changes(1))
    formats._fireUntil(None)
    assert [(ms, event) for ms, event, _a, _k in formats.sent] == [
        (0, FORMAT), (10, "program_state_update")]


def test_a_failed_format_notice_takes_nothing_with_it(formats):
    formats.fail = RuntimeError("the socket said no")
    _switchedAt(formats, 0)
    formats._fireUntil(None)
    formats.fail = None
    # Not sent again: the next one is the next switch's.
    formats._fireUntil(None)
    assert formats.sent == []
    _switchedAt(formats, 100)
    formats._fireUntil(None)
    assert formats.sent == [(100, FORMAT, [{}], {})]


def test_without_a_flusher_a_format_notice_is_not_kept(formats, monkeypatch):
    monkeypatch.setattr(WebServer, "_delta_running", False)
    WebServer.on_battle_format_change("team")
    assert WebServer._battle_format_notice is False
    assert formats.driver.due() is None


def _formatOnly(sends):
    with sends.cond:
        return [(event, payload) for _t, _at, event, payload in sends.sent
                if event == FORMAT]


def test_the_flusher_sends_a_format_notice_on_its_own_thread(flusher):
    flusher.start()
    thread = WebServer._delta_thread

    WebServer.on_battle_format_change("team")

    assert flusher.waitFor(1)
    assert _formatOnly(flusher) == [(FORMAT, {})]
    assert flusher.threads() == [thread]
    assert flusher.stop() is True


def test_a_format_notice_follows_the_deltas_from_the_flusher(
        flusher, monkeypatch):
    """The flusher's own take and send: a page that reads the format after
    the notice has the deltas of that flush by then."""
    monkeypatch.setattr(WebServer, "_delta_clock", _FrozenClock())
    flusher.start()
    WebServer.on_program_state_update(_changes(1))      # the leading edge
    assert flusher.waitFor(1)
    WebServer.on_program_state_update(_changes(2, 150))  # a 200 ms window
    WebServer.on_battle_format_change("crew")            # in it
    assert flusher.stop() is True

    assert [event for _t, _at, event, _p in flusher.sent] == [
        "program_state_update", "program_state_update", FORMAT]


def test_a_new_start_starts_with_no_format_notice(flusher, monkeypatch):
    flusher.start()
    assert flusher.stop() is True
    monkeypatch.setattr(WebServer, "_battle_format_notice", True)

    flusher.start()
    WebServer.on_held_change("score.1")
    assert flusher.waitFor(1)
    assert flusher.stop() is True
    assert _formatOnly(flusher) == []


def test_a_format_notice_is_queued_with_the_state_lock_held(flusher):
    # agent-pitfalls #17: a switch may be told under StateManager.lock (a
    # board operation's thread). Only queued there: the flusher sends it.
    from src.core.StateManager import StateManager

    flusher.start()
    arrived = []

    def writer():
        with StateManager.lock:
            WebServer.on_battle_format_change("team")
            arrived.append(flusher.waitFor(1, timeout=30))

    thread = threading.Thread(target=writer, daemon=True)
    thread.start()
    thread.join(60)
    assert not thread.is_alive()
    assert arrived == [True]
    assert flusher.stop() is True
