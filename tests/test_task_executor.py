"""The background task executor (src/core/TaskExecutor.py, ADR-005 S3f-1).

Pinned:

- the two lanes run on their own named threads, at most their size at a
  time, and none is started before the first submit;
- the nesting rule that keeps it free of starvation: nothing on a job-lane
  thread may submit a job, nothing on a fan-out thread may submit anything
  (NestedSubmitError) -- the task, its then / on_error and a done callback
  that runs there alike -- and with it, jobs that fill the job lane and all
  fan out still finish;
- map_fanout: results in order, `default` for a call that failed, ran past
  its deadline (abandoned) or did not start by the cap (cancelled); a
  call's deadline runs from its start, not from the submit (S3f-3 review);
- a task's exception is logged once, with its name, and carried on its
  Future; then / on_error deliver on the lane thread before the Future
  settles, their own failures logged once;
- shutdown: queued tasks cancelled (never run), running ones told to stop
  (`stopping`), a later submit cancelled rather than raised, refused on a
  lane thread, None waits for ever;
- the test seams: drain() waits for what tasks submit while running and for
  their deliveries, and a stepped executor runs its jobs only through
  run_next(), in order (or the newest first), one at a time even once a
  run_next() timed out on a step still running, never after a shutdown
  took its queue, and counts them (queued());
- both shells shut it down after the web server and before the flush, and
  the window's unused QThreadPool is gone.

Pure core: no Qt, no harness. Every executor made here is shut down at the
end of its test. Nothing here depends on how fast a thread is scheduled:
what must have happened is waited for on an event, and a deadline that must
have passed is the test's to set (map_fanout's, through its clock: _Clock).
"""
import ast
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pytest
from loguru import logger

from src.core import TaskExecutor as task_executor_module
from src.core.TaskExecutor import (
    FANOUT_THREAD_PREFIX, JOB_THREAD_PREFIX, NestedSubmitError, TaskExecutor)

PROJECT_ROOT = Path(__file__).resolve().parents[1]

WAIT = 10  # seconds; what a test waits at most for something that must happen


@pytest.fixture
def make():
    made = []

    def make(**kwargs):
        executor = TaskExecutor(**kwargs)
        made.append(executor)
        return executor

    yield make
    for executor in made:
        executor.shutdown(WAIT)


@contextmanager
def _records(level="DEBUG"):
    records = []
    sink = logger.add(lambda message: records.append(message.record),
                      level=level)
    try:
        yield records
    finally:
        logger.remove(sink)


def _errors(records):
    return [r for r in records if r["level"].name == "ERROR"]


def _lane_threads():
    # Thread objects, not names: an earlier test's executor names its
    # threads the same, and they may still be on their way out.
    return {thread for thread in threading.enumerate()
            if thread.name.startswith((JOB_THREAD_PREFIX,
                                       FANOUT_THREAD_PREFIX))}


def _try_submit(executor, how):
    """Submit from here the way `how` says; the exception's type name, or
    "ok"."""
    try:
        if how == "submit":
            executor.submit(lambda: None, task_name="nested").result(WAIT)
        elif how == "submit_fanout":
            executor.submit_fanout(lambda: None,
                                   task_name="nested").result(WAIT)
        else:
            executor.map_fanout([lambda: None], WAIT, task_name="nested")
    except Exception as error:
        return type(error).__name__
    return "ok"


# ── the lanes ────────────────────────────────────────────────────────────


def test_no_thread_is_started_before_the_first_submit(make):
    before = _lane_threads()
    executor = make()

    assert _lane_threads() - before == set()
    assert executor.submit(lambda: 1, task_name="first").result(WAIT) == 1
    assert _lane_threads() - before


def test_each_lane_runs_on_its_own_named_threads(make):
    executor = make()
    name = lambda: threading.current_thread().name  # noqa: E731

    assert executor.submit(name, task_name="job").result(WAIT).startswith(
        JOB_THREAD_PREFIX)
    assert executor.submit_fanout(
        name, task_name="fan-out").result(WAIT).startswith(
            FANOUT_THREAD_PREFIX)
    # Arguments are passed through -- `name` too: the executor's own label
    # is `task_name`, so it cannot collide with one of fn's.
    assert executor.submit(
        lambda a, b=0: a + b, 1, b=2, task_name="args").result(WAIT) == 3
    assert executor.submit(
        lambda name: name, name="fn's", task_name="label").result(WAIT) \
        == "fn's"
    assert executor.submit_fanout(
        lambda name: name, name="fn's", task_name="label").result(WAIT) \
        == "fn's"


@pytest.mark.parametrize("lane, size", [("job", 3), ("fan-out", 5)])
def test_a_lane_runs_at_most_its_size_at_a_time(make, lane, size):
    executor = make(jobWorkers=3, fanoutWorkers=5)
    submit = executor.submit if lane == "job" else executor.submit_fanout
    release = threading.Event()
    lock = threading.Lock()
    started = threading.Semaphore(0)
    running = [0]
    peak = [0]

    def task():
        with lock:
            running[0] += 1
            peak[0] = max(peak[0], running[0])
        started.release()
        release.wait(WAIT)
        with lock:
            running[0] -= 1

    futures = []
    try:
        futures = [submit(task, task_name=f"{lane}-{i}")
                   for i in range(size + 3)]
        for _ in range(size):
            assert started.acquire(timeout=WAIT)
        # The lane is full: the next task cannot have started.
        assert not started.acquire(timeout=0.2)
        assert running[0] == size
    finally:
        release.set()
    for future in futures:
        future.result(WAIT)
    assert peak[0] == size


def test_the_shells_get_the_designed_lane_sizes():
    # S3f decision 1: the job lane 4, the fan-out lane 8.
    assert (TaskExecutor.JOB_WORKERS, TaskExecutor.FANOUT_WORKERS) == (4, 8)


# ── the nesting rule ─────────────────────────────────────────────────────


def test_a_job_may_fan_out_but_not_submit_a_job(make):
    executor = make()

    def job():
        fanned = executor.submit_fanout(lambda: "fanned", task_name="inner")
        with pytest.raises(NestedSubmitError):
            executor.submit(lambda: None, task_name="nested job")
        return fanned.result(WAIT)

    assert executor.submit(job, task_name="outer").result(WAIT) == "fanned"


def test_a_fan_out_task_may_submit_nothing(make):
    executor = make()

    def task():
        return [_try_submit(executor, how)
                for how in ("submit", "submit_fanout", "map_fanout")]

    assert executor.submit_fanout(task, task_name="leaf").result(WAIT) == [
        "NestedSubmitError"] * 3


def test_a_refused_nested_submit_fails_the_job_that_made_it(make):
    executor = make()
    future = executor.submit(
        lambda: executor.submit(lambda: None, task_name="nested"),
        task_name="outer")

    assert isinstance(future.exception(WAIT), NestedSubmitError)


@pytest.mark.parametrize("lane", ["job", "fan-out", "stepped job"])
def test_a_done_callback_on_a_lane_thread_is_held_to_the_rule(make, lane):
    """The lane belongs to the thread, not only to the task: a done callback
    the Future runs on that thread (after the task returned) may not submit
    what the task may not."""
    executor = make(stepped=(lane == "stepped job"))
    submit = executor.submit_fanout if lane == "fan-out" else executor.submit
    release = threading.Event()
    seen = {}
    called = threading.Event()

    def onDone(_future):
        seen["thread"] = threading.current_thread().name
        seen["tries"] = [_try_submit(executor, how)
                         for how in ("submit", "submit_fanout")]
        called.set()

    future = submit(lambda: release.wait(WAIT), task_name="task")
    # Added before the task finishes, so the Future runs it where it
    # settles: on the lane thread.
    future.add_done_callback(onDone)
    if lane == "stepped job":
        stepper = threading.Thread(target=executor.run_next, args=(WAIT,))
        stepper.start()
    release.set()
    assert called.wait(WAIT)
    if lane == "stepped job":
        stepper.join(WAIT)

    assert seen["thread"].startswith(
        FANOUT_THREAD_PREFIX if lane == "fan-out" else JOB_THREAD_PREFIX)
    if lane == "fan-out":
        assert seen["tries"] == ["NestedSubmitError", "NestedSubmitError"]
    else:
        # A job-lane thread may fan out, never queue a job.
        assert seen["tries"] == ["NestedSubmitError", "ok"]


def test_jobs_that_fill_the_job_lane_and_all_fan_out_still_finish(make):
    """The starvation the two lanes exist for: on one pool, jobs that fill
    it and wait for requests queued behind them never finish."""
    executor = make(jobWorkers=2, fanoutWorkers=2)
    together = threading.Barrier(2, timeout=WAIT)

    def job(n):
        together.wait()  # both job threads busy before either fans out
        return executor.map_fanout(
            [lambda i=i: n * 10 + i for i in range(3)], WAIT,
            task_name=f"job{n}")

    futures = [executor.submit(job, n, task_name=f"job{n}") for n in (1, 2)]
    # ...and one more job queued behind them.
    futures.append(executor.submit(
        lambda: executor.map_fanout([lambda: "late"], WAIT,
                                    task_name="job3"),
        task_name="job3"))

    assert [future.result(WAIT) for future in futures] == [
        [10, 11, 12], [20, 21, 22], ["late"]]


def test_a_thread_outside_the_executor_may_do_both(make):
    executor = make()

    assert executor.submit(lambda: 1, task_name="job").result(WAIT) == 1
    assert executor.map_fanout(
        [lambda: 1, lambda: 2], WAIT, task_name="outside") == [1, 2]


# ── map_fanout ───────────────────────────────────────────────────────────


def test_map_fanout_returns_the_results_in_order(make):
    executor = make()
    calls = [lambda d=d: time.sleep(d) or d for d in (0.2, 0.0, 0.1)]

    assert executor.map_fanout(calls, WAIT, task_name="ordered") \
        == [0.2, 0.0, 0.1]
    assert executor.map_fanout([], WAIT, task_name="nothing") == []


class _Clock:
    """map_fanout's clock (`_clock`), moved by the test: a deadline passes
    when the test says so, however the threads were scheduled."""

    def __init__(self, executor):
        self.now = 0.0
        self._executor = executor
        self._reads = threading.Condition()
        self._latest = None

    def __call__(self):
        now = self.now
        with self._reads:
            self._latest = now
            self._reads.notify_all()
        return now

    def advance(self, to):
        """Move to `to` and wake map_fanout's wait."""
        with self._executor._cond:
            self.now = to
            self._executor._cond.notify_all()

    def seen(self, at):
        """Wait until the clock was read at `at` or later -- by the wait,
        when nothing starts then -- and the wait is done with that look (it
        holds the executor's condition from the read until it waits again
        or returns)."""
        with self._reads:
            assert self._reads.wait_for(
                lambda: self._latest is not None and self._latest >= at,
                WAIT)
        with self._executor._cond:
            pass


def _mapInThread(executor, calls, timeout, task_name, default):
    box = {}
    caller = threading.Thread(target=lambda: box.update(
        results=executor.map_fanout(calls, timeout, task_name=task_name,
                                    default=default)))
    return caller, box


def test_map_fanout_gives_the_default_for_a_failure_a_late_call_and_one_never_started(
        make, monkeypatch):
    """The two slow calls run past their deadline once both lane threads
    are known to be busy with them -- so the first two have finished and
    the fifth has not started. The fifth is still waited for (it has the
    whole timeout once it starts) until the cap, then cancelled."""
    executor = make(fanoutWorkers=2)
    clock = _Clock(executor)
    monkeypatch.setattr(task_executor_module, "_clock", clock)
    release = threading.Event()
    slowStarted = threading.Semaphore(0)
    ran = []

    def fail():
        raise ValueError("probe")

    def slow():
        slowStarted.release()
        release.wait(WAIT)
        return "slow"

    # FIFO on two threads: "ok" and fail run first, the two slow calls take
    # both threads only after those finished, the fifth waits behind them.
    calls = [lambda: "ok", fail, slow, slow,
             lambda: ran.append("queued") or "queued"]
    caller, box = _mapInThread(executor, calls, 10, "mixed", {})
    try:
        with _records("WARNING") as records:
            caller.start()
            for _ in range(2):
                assert slowStarted.acquire(timeout=WAIT)   # both at 0
            clock.advance(11)        # past both slow calls' deadline
            clock.seen(11)
            assert caller.is_alive()  # the fifth could still start
            clock.advance(30)        # the cap: 3 timeouts
            caller.join(WAIT)
    finally:
        release.set()

    assert box["results"] == ["ok", {}, {}, {}, {}]
    # The fifth was cancelled, never run; the two slow ones were abandoned.
    assert executor.drain(WAIT)
    assert ran == []
    late = [r for r in records
            if r["level"].name == "WARNING" and "'mixed'" in r["message"]]
    assert len(late) == 1
    assert "3 of 5 did not finish: 2 ran past 10 s, 1 did not start " \
        "within 30 s" in late[0]["message"]


def test_a_fan_out_calls_deadline_runs_from_its_start_not_its_submit(
        make, monkeypatch):
    """One lane thread: the second call waits behind the first for most of
    the timeout, then runs past what is left of it -- but within its own
    timeout from its start, so it is waited for. Until the S3f-3 review the
    deadline counted the queue time: on a busy lane, a fetch's requests
    expired before they ran."""
    executor = make(fanoutWorkers=1)
    clock = _Clock(executor)
    monkeypatch.setattr(task_executor_module, "_clock", clock)
    started = {name: threading.Event() for name in "ab"}
    release = {name: threading.Event() for name in "ab"}

    def call(name):
        started[name].set()
        release[name].wait(WAIT)
        return name

    caller, box = _mapInThread(
        executor, [lambda: call("a"), lambda: call("b")], 100, "queued",
        "late")
    try:
        with _records("WARNING") as records:
            caller.start()
            assert started["a"].wait(WAIT)   # at 0
            clock.advance(90)
            release["a"].set()
            assert started["b"].wait(WAIT)   # at 90: its deadline is 190
            clock.advance(150)               # 150 after the submit
            clock.seen(150)
            assert caller.is_alive()
            release["b"].set()
            caller.join(WAIT)
    finally:
        for event in release.values():
            event.set()

    assert box["results"] == ["a", "b"]
    assert not [r for r in records if "'queued'" in r["message"]]


# ── errors and delivery ──────────────────────────────────────────────────


@pytest.mark.parametrize("lane", ["job", "fan-out"])
def test_an_exception_is_logged_once_with_the_name_and_carried(make, lane):
    executor = make()
    submit = executor.submit if lane == "job" else executor.submit_fanout

    def fail():
        raise ValueError("probe failure")

    with _records() as records:
        future = submit(fail, task_name="probe-task")
        error = future.exception(WAIT)
        with pytest.raises(ValueError):
            future.result()
        assert executor.drain(WAIT)

    assert isinstance(error, ValueError)
    errors = _errors(records)
    assert len(errors) == 1
    assert "probe-task" in errors[0]["message"]
    assert lane in errors[0]["message"]
    assert errors[0]["exception"] is not None  # the traceback is in the log


def test_a_failed_fan_out_call_is_logged_once_and_becomes_the_default(make):
    executor = make()

    def fail():
        raise ValueError("probe failure")

    with _records() as records:
        results = executor.map_fanout([lambda: 1, fail], WAIT,
                                      task_name="pages", default=None)

    assert results == [1, None]
    errors = _errors(records)
    assert len(errors) == 1
    assert "pages[1]" in errors[0]["message"]


def test_then_and_on_error_deliver_on_the_lane_thread_before_settling(make):
    executor = make()
    seen = {}

    def then(result):
        seen["then"] = (result, threading.current_thread().name)

    def onError(error):
        seen["on_error"] = (type(error).__name__,
                            threading.current_thread().name)

    def fail():
        raise ValueError("probe")

    ok = executor.submit(lambda: 7, task_name="ok", then=then,
                         on_error=onError)
    failed = executor.submit(fail, task_name="fail", then=then,
                             on_error=onError)
    # Settled means delivered: then / on_error ran before the Future did.
    assert ok.result(WAIT) == 7
    assert isinstance(failed.exception(WAIT), ValueError)

    assert set(seen) == {"then", "on_error"}
    assert seen["then"][0] == 7
    assert seen["on_error"][0] == "ValueError"
    assert all(thread.startswith(JOB_THREAD_PREFIX)
               for _value, thread in seen.values())


def test_a_failing_delivery_is_logged_once_and_the_task_keeps_its_result(
        make):
    executor = make()

    def then(result):
        # Not a place to submit from either: the job lane's rule.
        executor.submit(lambda: None, task_name="from then")

    def onError(error):
        raise RuntimeError("delivery broke")

    def fail():
        raise ValueError("probe")

    with _records() as records:
        ok = executor.submit(lambda: 7, task_name="answer", then=then)
        failed = executor.submit(fail, task_name="broken", on_error=onError)
        assert ok.result(WAIT) == 7
        assert isinstance(failed.exception(WAIT), ValueError)
        assert executor.drain(WAIT)

    messages = [r["message"] for r in _errors(records)]
    assert sorted(messages) == sorted([
        "TaskExecutor: job 'answer': then failed",
        "TaskExecutor: job 'broken' failed",
        "TaskExecutor: job 'broken': on_error failed",
    ])


def test_drain_waits_for_the_delivery(make):
    executor = make()
    delivered = []

    executor.submit(lambda: 1, task_name="answer",
                    then=lambda result: time.sleep(0.3)
                    or delivered.append(result))

    assert executor.drain(WAIT) is True
    assert delivered == [1]


# ── shutdown ─────────────────────────────────────────────────────────────


def test_shutdown_cancels_the_queue_and_stops_the_running_tasks(make):
    executor = make(jobWorkers=2)
    started = threading.Semaphore(0)
    ran = []
    delivered = []

    def running():
        started.release()
        executor.stopping.wait(WAIT)
        return "stopped"

    runners = [executor.submit(running, task_name=f"running-{i}")
               for i in (1, 2)]
    for _ in runners:
        assert started.acquire(timeout=WAIT)
    queued = [executor.submit(lambda: ran.append("queued"),
                              task_name=f"queued-{i}",
                              then=delivered.append,
                              on_error=delivered.append)
              for i in (1, 2, 3)]

    assert executor.shutdown(WAIT) is True

    assert executor.stopping.is_set()
    assert [future.result(0) for future in runners] == ["stopped", "stopped"]
    assert all(future.cancelled() for future in queued)
    # A cancelled task runs neither the task nor its delivery.
    assert ran == [] and delivered == []


def test_a_submit_after_the_shutdown_is_cancelled_not_raised(make):
    executor = make()
    executor.shutdown(WAIT)

    with _records() as records:
        job = executor.submit(lambda: 1, task_name="late job")
        fanned = executor.submit_fanout(lambda: 1, task_name="late fan-out")
        mapped = executor.map_fanout([lambda: 1, lambda: 2], WAIT,
                                     task_name="late map", default="none")

    assert job.cancelled() and fanned.cancelled()
    assert mapped == ["none", "none"]
    assert not [r for r in records if r["level"].no >= 30]  # nothing loud


def test_a_job_waiting_on_its_fan_out_returns_at_once_on_shutdown(make):
    """The shutdown drops the queued fan-out calls without waking
    concurrent.futures.wait(); map_fanout must still see them cancelled,
    not sit out its timeout."""
    executor = make(jobWorkers=1, fanoutWorkers=1)
    started = threading.Event()

    def first():
        started.set()
        executor.stopping.wait(WAIT)
        return "stopped"

    job = executor.submit(
        lambda: executor.map_fanout([first, lambda: "queued"], WAIT,
                                    task_name="pages", default=None),
        task_name="job")
    assert started.wait(WAIT)

    began = time.monotonic()
    assert executor.shutdown(WAIT) is True
    assert time.monotonic() - began < WAIT / 2
    # The running call answered the stop; the queued one was cancelled.
    assert job.result(0) == ["stopped", None]


def test_shutdown_reports_a_task_that_does_not_stop_in_time(make):
    executor = make()
    release = threading.Event()
    started = threading.Event()

    def stubborn():
        started.set()
        release.wait(WAIT)  # ignores `stopping`

    future = executor.submit(stubborn, task_name="stubborn")
    assert started.wait(WAIT)
    try:
        with _records("WARNING") as records:
            assert executor.shutdown(0.2) is False
    finally:
        release.set()

    assert any("stubborn" in r["message"] for r in records)
    future.result(WAIT)
    assert executor.shutdown(WAIT) is True  # a second call waits again


@pytest.mark.parametrize("lane", ["job", "fan-out"])
def test_shutdown_on_a_lane_thread_is_refused(make, lane):
    # It would wait for its own task until the timeout.
    executor = make()
    submit = executor.submit if lane == "job" else executor.submit_fanout
    future = submit(lambda: executor.shutdown(WAIT), task_name="self-stop")

    assert isinstance(future.exception(WAIT), NestedSubmitError)
    assert not executor.stopping.is_set()  # refused before doing anything


def test_shutdown_and_drain_with_no_timeout_wait_for_ever(make):
    executor = make()
    release = threading.Event()
    executor.submit(lambda: release.wait(WAIT), task_name="short")
    timer = threading.Timer(0.2, release.set)
    timer.start()
    try:
        assert executor.drain(None) is True
        assert executor.shutdown(None) is True
    finally:
        timer.cancel()
        release.set()


# ── the test seams ───────────────────────────────────────────────────────


def test_drain_waits_for_what_a_task_submits_while_running(make):
    executor = make()
    done = []

    def job():
        # Not waited for by the job: drain() must still wait for it.
        executor.submit_fanout(
            lambda: time.sleep(0.3) or done.append("fanned"),
            task_name="tail")
        return "job"

    executor.submit(job, task_name="job")

    assert executor.drain(WAIT) is True
    assert done == ["fanned"]
    assert executor.drain(WAIT) is True  # nothing left: at once


def test_drain_times_out_on_a_task_that_does_not_finish(make):
    executor = make()
    release = threading.Event()
    executor.submit(lambda: release.wait(WAIT), task_name="blocked")
    try:
        assert executor.drain(0.2) is False
    finally:
        release.set()
    assert executor.drain(WAIT) is True


def test_drain_from_a_lane_thread_is_refused(make):
    executor = make()
    future = executor.submit(lambda: executor.drain(WAIT),
                             task_name="self-drain")

    assert isinstance(future.exception(WAIT), NestedSubmitError)


def test_a_stepped_executor_runs_its_jobs_only_through_run_next(make):
    executor = make(stepped=True)
    order = []

    def job(n):
        order.append((n, threading.current_thread().name))
        # A stepped job still fans out on the real lane, and the nesting
        # rule still holds.
        with pytest.raises(NestedSubmitError):
            executor.submit(lambda: None, task_name="nested")
        return executor.map_fanout([lambda: n], WAIT, task_name=f"job{n}")

    futures = [executor.submit(job, n, task_name=f"job{n}")
               for n in (1, 2, 3)]
    assert order == [] and not any(future.done() for future in futures)

    assert executor.run_next() is True
    assert [n for n, _thread in order] == [1]
    assert futures[0].result(0) == [1]
    assert order[0][1].startswith(JOB_THREAD_PREFIX)

    assert executor.drain(WAIT) is True  # runs the rest, in order
    assert [n for n, _thread in order] == [1, 2, 3]
    assert [future.result(0) for future in futures] == [[1], [2], [3]]
    assert executor.run_next() is False


def test_a_stepped_job_cancelled_while_queued_is_skipped(make):
    executor = make(stepped=True)
    ran = []
    first = executor.submit(lambda: ran.append(1), task_name="first")
    executor.submit(lambda: ran.append(2), task_name="second")

    assert first.cancel()
    assert executor.run_next() is True
    assert ran == [2]
    assert executor.run_next() is False


def test_a_stepped_executor_never_runs_a_job_after_the_shutdown(make):
    """A job run_next() claimed is running: the shutdown waits for it. One
    it had not claimed is cancelled, and run_next() runs nothing after."""
    executor = make(stepped=True)
    started = threading.Event()
    ran = []

    def running():
        started.set()
        executor.stopping.wait(WAIT)
        return "stopped"

    claimed = executor.submit(running, task_name="claimed")
    queued = executor.submit(lambda: ran.append("queued"), task_name="queued")
    stepper = threading.Thread(target=executor.run_next, args=(WAIT,))
    stepper.start()
    assert started.wait(WAIT)

    assert executor.shutdown(WAIT) is True
    stepper.join(WAIT)

    assert claimed.result(0) == "stopped"
    assert queued.cancelled()
    assert executor.run_next() is False
    assert ran == []


def test_a_step_that_outlived_its_run_next_is_waited_for_before_the_next(
        make):
    """ADR-005 S3f-4 review: drain_tasks retries run_next() with a short
    timeout while it pumps the GUI thread. A step that timed out is still
    running; the retry waits for it -- raising TimeoutError again while it
    runs -- and starts the next step only once it is over."""
    executor = make(stepped=True)
    release = threading.Event()
    running = []

    def first():
        running.append("first")
        release.wait(WAIT)
        running.remove("first")

    def second():
        running.append("second")
        return list(running)

    executor.submit(first, task_name="first")
    later = executor.submit(second, task_name="second")

    with pytest.raises(TimeoutError):
        executor.run_next(0.05)            # first, still running
    with pytest.raises(TimeoutError):
        executor.run_next(0.05)            # waits for first; not second
    assert running == ["first"] and executor.queued() == 1
    assert executor.drain(0.05) is False   # the same wait, as drain() has it
    assert not later.done()

    release.set()
    assert executor.run_next(WAIT) is True
    assert later.result(0) == ["second"]   # never alongside first
    assert executor.drain(WAIT) is True


def test_run_next_is_for_a_stepped_executor_only(make):
    with pytest.raises(RuntimeError):
        make().run_next()
    with pytest.raises(RuntimeError):
        make().queued()


def test_a_stepped_executor_counts_its_queue_and_can_run_the_newest(make):
    """ADR-005 S3f-2: queued() counts what waits for run_next(), and
    run_next(newest=True) runs the last one submitted first -- a test
    making a later answer overtake an earlier one."""
    executor = make(stepped=True)
    ran = []
    for n in (1, 2, 3):
        executor.submit(ran.append, n, task_name=f"job{n}")
    assert executor.queued() == 3

    assert executor.run_next(newest=True) is True
    assert ran == [3] and executor.queued() == 2
    assert executor.run_next() is True
    assert ran == [3, 1] and executor.queued() == 1
    assert executor.drain(WAIT) is True
    assert ran == [3, 1, 2] and executor.queued() == 0


# ── the shells ───────────────────────────────────────────────────────────


def _function(path, name):
    tree = ast.parse((PROJECT_ROOT / path).read_text(encoding="utf-8"))
    return next(node for node in ast.walk(tree)
                if isinstance(node, ast.FunctionDef) and node.name == name)


def _calls_in_order(function):
    calls = [node for node in ast.walk(function)
             if isinstance(node, ast.Call)]
    calls.sort(key=lambda node: (node.lineno, node.col_offset))
    return [ast.unparse(node.func) for node in calls]


@pytest.mark.parametrize("path, function, stop, flush", [
    ("src/Autona.py", "closeEvent", "self.webserver.stop", None),
    ("src/headless.py", "Shutdown", "self.webserver.stop",
     "StateManager.JoinExports"),
])
def test_both_shells_stop_the_tasks_after_serving_and_before_the_flush(
        path, function, stop, flush):
    calls = _calls_in_order(_function(path, function))
    shutdowns = [i for i, call in enumerate(calls)
                 if call == "TaskExecutor.instance.shutdown"]

    assert len(shutdowns) == 1, calls
    assert calls.index(stop) < shutdowns[0]
    if flush is not None:
        assert shutdowns[0] < calls.index(flush)
        assert shutdowns[0] < calls.index("StatePersistence.Flush")
    # The desktop flushes in the interpreter's exit join, after closeEvent
    # (the export and state writers are non-daemon threads): nothing in it
    # may flush before the tasks are stopped.
    else:
        assert "StateManager.JoinExports" not in calls
        assert "StatePersistence.Flush" not in calls


def test_the_window_has_no_thread_pool_of_its_own():
    # Window.threadpool was never used (ADR-005 S3f inventory).
    source = (PROJECT_ROOT / "src" / "Autona.py").read_text(encoding="utf-8")
    assert "QThreadPool(" not in source
    assert "self.threadpool" not in source
