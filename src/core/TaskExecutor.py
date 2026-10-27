"""The one place long-running work runs: two named thread-pool lanes
(ADR-005 S3f).

Until S3f the app ran its background work on whatever was at hand -- three
QThreadPools, QRunnable Workers polled with ``Worker.wait_for_all``, a
ThreadPoolExecutor built per call -- with no common shutdown and with jobs
that queued more jobs on the pool they were running on and waited for them.
Once enough of those ran at the same time, every thread of the pool was a
job waiting for work queued behind it (starvation; with no timeout on the
wait, a deadlock). This module replaces them, step by step (S3f-2..4).

Two lanes, one executor (S3f decision 1):

- **job** (``submit``, ``JOB_WORKERS`` threads): the work a caller hands off
  and gets an answer from later -- a provider fetch, an asset load, an
  image decode.
- **fan-out** (``submit_fanout`` / ``map_fanout``, ``FANOUT_WORKERS``
  threads): the parallel requests a job makes and waits for (the pages of
  a set list, the two APIs of a set).

The rule that keeps it free of starvation is enforced, not documented: on a
job-lane thread nothing may submit a job, and on a fan-out thread nothing
may submit anything (``NestedSubmitError``). The lane is a property of the
thread for its whole life, so the rule covers the task, its ``then`` /
``on_error`` and any Future done callback that runs there. So the only waits
are a job (or a thread outside the executor) waiting for fan-out tasks, and
a fan-out task waits for nothing -- the fan-out lane always drains, so every
job finishes, however full the job lane is. A thread outside both lanes
(the GUI thread, a web request) may fan out too: it holds no lane thread,
so it cannot starve one -- but it blocks while it waits, which the GUI
thread must not do.

Delivering a job's answer: ``submit(..., then=, on_error=)``. ``then(result)``
or ``on_error(error)`` runs on the lane thread, inside the task, before its
Future settles -- so ``drain()`` and ``shutdown()`` wait for it, and an
exception it raises is logged once, with the task's name. Hand the answer on
from there (``gui_executor(...)`` to the GUI thread); do not submit from it
(the nesting rule). ``Future.add_done_callback`` is not the delivery path:
it runs after the Future settled (``drain()`` may already have returned),
and an exception in it goes to the stdlib ``concurrent.futures`` logger, not
to loguru's logs. A task the shutdown cancelled runs neither.

Errors: a task's exception is logged once, with its ``task_name``, where it
happened, and carried on its Future. Whoever reads the Future
(``result()``) owns what to do next and does not log it again;
``map_fanout`` substitutes its ``default``.

Shutdown (``shutdown``): both shells call it once they stop serving, before
``./out`` and ``user_data/state.json`` are flushed. Queued tasks are
cancelled; ``stopping`` is set for the running ones, which a long job checks
between requests; a task submitted afterwards gets an already cancelled
Future (no exception: a timer or a late reply can still try to submit while
the shell winds down, and its done callbacks see the cancellation). A task
still running when the wait times out keeps its thread: the interpreter's
exit joins the pools' threads (concurrent.futures has no daemon threads),
so it delays the exit until it returns -- for ever, if it waits on
something that will not come once the event loop has stopped (a GUI-thread
marshal without a timeout). A job must not do that.

Waiting on the Futures: ``result()`` / ``exception()`` /
``add_done_callback`` and ``map_fanout`` see a cancellation at once.
``concurrent.futures.wait`` / ``as_completed`` do not see one the shutdown
made (CPython wakes them only when a worker picks the cancelled task up,
and the shutdown drops the queued ones), so do not wait on these Futures
with them.

Test seams:

- ``drain(timeout)``: wait until everything submitted -- including what the
  tasks submitted while running, and their ``then`` / ``on_error`` -- has
  finished. It does not run the Qt event loop: an answer ``then`` hands to
  the GUI thread is delivered when the test processes the events.
- ``TaskExecutor(stepped=True)``: job-lane tasks are queued and run only by
  ``run_next()``, one at a time and in submission order, each on a thread
  of its own; the fan-out lane runs as usual. One schedule, the same on
  every run.

Threads are created on the first submit to a lane (ThreadPoolExecutor), not
by the constructor. ``TaskExecutor.instance`` is created by
``bootstrap_core()``.

Qt-free (tests/test_core_qt_free.py). Import side effects: none
(agent-pitfalls #1).
"""
import collections
import functools
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor

from loguru import logger

JOB = "job"
FANOUT = "fan-out"

JOB_THREAD_PREFIX = "AutonaJob"
FANOUT_THREAD_PREFIX = "AutonaFanout"


class NestedSubmitError(RuntimeError):
    """A submit (or a wait) from a lane thread that may not make it: a job
    from a job-lane thread, anything from a fan-out thread, a drain / a
    shutdown / a step from any lane thread (see the module docstring). A
    programming error, raised where it was made."""


class TaskExecutor:
    instance = None

    JOB_WORKERS = 4
    FANOUT_WORKERS = 8

    def __init__(self, jobWorkers=JOB_WORKERS, fanoutWorkers=FANOUT_WORKERS,
                 stepped=False):
        # Which lane the current thread belongs to (None outside both). Set
        # once per thread, for its whole life, by the pools' initializer (and
        # by a stepped job's own thread): the task, its then / on_error and
        # the done callbacks that run there are all on that lane. Per
        # executor, so two executors (tests) do not see each other's threads.
        self._local = threading.local()
        self._pools = {
            lane: ThreadPoolExecutor(
                max_workers=workers, thread_name_prefix=prefix,
                initializer=self._MarkLane, initargs=(lane,))
            for lane, workers, prefix in (
                (JOB, jobWorkers, JOB_THREAD_PREFIX),
                (FANOUT, fanoutWorkers, FANOUT_THREAD_PREFIX))
        }
        self._stepped = stepped
        # Stepped mode: the job-lane tasks run_next() has yet to run, as
        # (future, runner), oldest first.
        self._steps = collections.deque()
        # Every task submitted and not finished yet (queued, stepped or
        # running): Future -> task name. drain() and shutdown() wait on it.
        self._pending = {}
        self._cond = threading.Condition()
        self._closed = False
        # Set by shutdown() once the queues are cancelled. A long job checks
        # it between requests and returns early.
        self.stopping = threading.Event()

    # ── submitting ───────────────────────────────────────────────────────

    def submit(self, fn, *args, task_name, then=None, on_error=None,
               **kwargs):
        """Run ``fn(*args, **kwargs)`` on the job lane; its Future.

        ``task_name`` says what it is in the logs (named so that it cannot
        collide with an argument of ``fn``). ``then(result)`` /
        ``on_error(error)`` deliver the answer: they run on the lane thread,
        before the Future settles (see the module docstring).

        Raises NestedSubmitError on a lane thread of this executor: a job
        fans out (``submit_fanout`` / ``map_fanout``), it does not queue a
        job.
        """
        lane = self._Lane()
        if lane is not None:
            raise NestedSubmitError(
                f"'{task_name}': a job submitted from a {lane} thread")
        return self._Submit(JOB, task_name, fn, args, kwargs, then, on_error)

    def submit_fanout(self, fn, *args, task_name, **kwargs):
        """Run ``fn(*args, **kwargs)`` on the fan-out lane; its Future.

        For the requests a job makes in parallel and waits for: the job
        reads the answers, so there is no ``then``. Raises NestedSubmitError
        on a fan-out thread (fan-out tasks are the leaves: they never wait
        on this executor).
        """
        if self._Lane() == FANOUT:
            raise NestedSubmitError(
                f"'{task_name}': a fan-out submitted from a {FANOUT} thread")
        return self._Submit(FANOUT, task_name, fn, args, kwargs, None, None)

    def map_fanout(self, calls, timeout, *, task_name, default=None):
        """Run every zero-argument callable in ``calls`` on the fan-out
        lane, wait for them (at most ``timeout`` seconds in all; None waits
        for ever) and return their results in the same order.

        A call that raised (logged once, by the lane), was cancelled (the
        executor shut down) or had not finished by the deadline is
        ``default`` in the list. One that had not started by then is
        cancelled; one that had is abandoned (its result is dropped).
        """
        futures = [self.submit_fanout(call, task_name=f"{task_name}[{index}]")
                   for index, call in enumerate(calls)]
        # Waited for through done callbacks, not concurrent.futures.wait():
        # see _WaitAll.
        _WaitAll(futures, timeout)
        results = []
        late = []
        for future in futures:
            if not future.done():
                late.append(future)
                results.append(default)
            elif future.cancelled() or future.exception() is not None:
                results.append(default)
            else:
                results.append(future.result())
        if late:
            for future in late:
                future.cancel()
            logger.warning(
                f"TaskExecutor: {FANOUT} '{task_name}': {len(late)} of "
                f"{len(futures)} did not finish within {timeout} s")
        return results

    # ── stopping and waiting ─────────────────────────────────────────────

    def shutdown(self, timeout):
        """Cancel what is queued, tell the running tasks to stop
        (``stopping``) and wait up to ``timeout`` seconds for them (None:
        for ever). True if nothing was left running. Later submits get a
        cancelled Future.

        Safe to call more than once (a later call only waits again). Raises
        NestedSubmitError on a lane thread, where it would wait for its own
        task."""
        if self._Lane() is not None:
            raise NestedSubmitError("shutdown() from a lane thread")
        with self._cond:
            self._closed = True
            steps = list(self._steps)
            self._steps.clear()
        # Outside the condition: a cancel runs the Future's done callback,
        # which takes it.
        for future, _runner in steps:
            future.cancel()
        for pool in self._pools.values():
            pool.shutdown(wait=False, cancel_futures=True)
        # Only once the queues are empty: a task that stops early on this
        # must not free its thread for a queued one.
        self.stopping.set()
        idle = self._WaitIdle(_Deadline(timeout))
        if not idle:
            with self._cond:
                running = sorted(self._pending.values())
            logger.warning(
                f"TaskExecutor: still running after {timeout} s: {running}")
        return bool(idle)

    def drain(self, timeout):
        """Wait until every task submitted so far -- and every task those
        submit while running, and every ``then`` / ``on_error`` -- has
        finished; True if it did within ``timeout`` seconds (None: for
        ever). In stepped mode, runs the queued jobs first (``run_next``). A
        test seam; raises NestedSubmitError on a lane thread (it would wait
        for itself)."""
        if self._Lane() is not None:
            raise NestedSubmitError("drain() from a lane thread")
        deadline = _Deadline(timeout)
        while True:
            if self._stepped:
                try:
                    while self.run_next(_Remaining(deadline)):
                        pass
                except TimeoutError:
                    return False
            idle = self._WaitIdle(deadline)
            if idle is not None:
                return idle
            # A job was queued while waiting (stepped mode): run it too.

    def run_next(self, timeout=30.0):
        """Stepped mode: run the oldest queued job on a thread of its own and
        wait for it to finish (``timeout`` seconds; None: for ever). False
        when no job is queued or the executor is shut down; TimeoutError if
        it did not finish in time."""
        if not self._stepped:
            raise RuntimeError("run_next() is for a stepped TaskExecutor")
        if self._Lane() is not None:
            raise NestedSubmitError("run_next() from a lane thread")
        with self._cond:
            while True:
                # Checked under the condition shutdown() closes under: a job
                # is either claimed here (running, so the shutdown waits for
                # it, as for a pool thread's task) or cancelled there --
                # never run after the shutdown took the queue.
                if self._closed or not self._steps:
                    return False
                future, runner = self._steps.popleft()
                taskName = self._pending.get(future)
                # False: cancelled while queued (it has settled); next one.
                if future.set_running_or_notify_cancel():
                    break
        thread = threading.Thread(
            target=self._RunStep, args=(future, runner),
            name=f"{JOB_THREAD_PREFIX}Step", daemon=True)
        thread.start()
        thread.join(timeout)
        if thread.is_alive():
            raise TimeoutError(
                f"TaskExecutor: {JOB} '{taskName}' did not finish within "
                f"{timeout} s")
        return True

    # ── internals ────────────────────────────────────────────────────────

    def _Lane(self):
        return getattr(self._local, "lane", None)

    def _MarkLane(self, lane):
        self._local.lane = lane

    def _Submit(self, lane, taskName, fn, args, kwargs, then, onError):
        runner = functools.partial(
            self._Run, lane, taskName, fn, args, kwargs, then, onError)
        with self._cond:
            if self._closed:
                logger.debug(
                    f"TaskExecutor: {lane} '{taskName}' submitted after "
                    "shutdown; cancelled")
                future = Future()
                future.cancel()
                return future
            if lane == JOB and self._stepped:
                future = Future()
                self._steps.append((future, runner))
            else:
                # Under the condition, so shutdown() cannot close the pool
                # between the check above and this. (No deadlock with the
                # done callback, which takes the condition: shutdown() only
                # reaches the pools -- and their cancel callbacks -- after
                # it has set _closed, so no submit is inside a pool then.)
                future = self._pools[lane].submit(runner)
            self._pending[future] = taskName
            self._cond.notify_all()
        # After releasing the condition: a Future that is already done runs
        # the callback right here.
        future.add_done_callback(self._Settled)
        return future

    @staticmethod
    def _Run(lane, taskName, fn, args, kwargs, then, onError):
        try:
            result = fn(*args, **kwargs)
        except Exception as error:
            # The one log of this failure; the Future carries it on.
            logger.opt(exception=True).error(
                f"TaskExecutor: {lane} '{taskName}' failed")
            if onError is not None:
                _Deliver(onError, error, lane, taskName, "on_error")
            raise
        if then is not None:
            _Deliver(then, result, lane, taskName, "then")
        return result

    def _RunStep(self, future, runner):
        # What ThreadPoolExecutor's worker does with a task, on a thread of
        # the job lane; run_next() has already claimed the Future (running).
        self._MarkLane(JOB)
        try:
            result = runner()
        except BaseException as error:
            future.set_exception(error)
        else:
            future.set_result(result)

    def _Settled(self, future):
        with self._cond:
            self._pending.pop(future, None)
            self._cond.notify_all()

    def _WaitIdle(self, deadline):
        """Wait until nothing submitted is unfinished: True. False at the
        deadline (None: none). None when a stepped job is queued (only
        run_next() runs it, so waiting would not see it finish)."""
        with self._cond:
            while True:
                if self._stepped and self._steps and not self._closed:
                    return None
                if not self._pending:
                    return True
                remaining = _Remaining(deadline)
                if remaining is not None and remaining <= 0:
                    return False
                self._cond.wait(remaining)


def _Deadline(timeout):
    return None if timeout is None else time.monotonic() + timeout


def _Remaining(deadline):
    return None if deadline is None else max(0.0, deadline - time.monotonic())


def _Deliver(callback, value, lane, taskName, what):
    # A delivery hook's failure is logged once, with the task's name, and
    # goes no further: the Future carries the task's own outcome.
    try:
        callback(value)
    except Exception:
        logger.opt(exception=True).error(
            f"TaskExecutor: {lane} '{taskName}': {what} failed")


def _WaitAll(futures, timeout):
    """Wait until every Future in `futures` is done or cancelled, at most
    `timeout` seconds (None: for ever).

    Not concurrent.futures.wait() / as_completed(): they only see a
    cancellation once a worker has picked the cancelled task up
    (set_running_or_notify_cancel), and the shutdown's cancel_futures drops
    the queued tasks without that -- a job waiting on its fan-out would sit
    out its whole timeout. A done callback runs on cancel()."""
    if not futures:
        return
    lock = threading.Lock()
    remaining = [len(futures)]
    settled = threading.Event()

    def onDone(_future):
        with lock:
            remaining[0] -= 1
            if remaining[0] == 0:
                settled.set()

    for future in futures:
        future.add_done_callback(onDone)
    settled.wait(timeout)
