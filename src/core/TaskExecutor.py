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
something that will not come once the event loop has stopped. A job must
not do that: the one GUI-thread wait a job may make, the shell's marshal
(GuiExecutor.run_sync, ADR-005 S3f-4), gives up once ``stopping`` is set or
the event loop has exited, and a job that is about to start work ending in
one checks for that first (the game load).

Waiting on the Futures: ``result()`` / ``exception()`` /
``add_done_callback`` and ``map_fanout`` see a cancellation at once.
``concurrent.futures.wait`` / ``as_completed`` do not see one the shutdown
made (CPython wakes them only when a worker picks the cancelled task up
(set_running_or_notify_cancel), and the shutdown's cancel_futures drops the
queued ones without that), so do not wait on these Futures with them: a job
waiting on its fan-out would sit out its whole timeout. ``map_fanout``
waits on the executor's own condition, which every settle notifies.

``map_fanout``'s deadline is each call's own, counted from when the call
starts running, and the whole wait has a cap (``FANOUT_CAP_FACTOR``
timeouts): the fan-out lane is shared, and a deadline that counted the time
a call spent queued behind other fetches' requests was used up before the
call ran (S3f-3 review).

Test seams:

- ``drain(timeout)``: wait until everything submitted -- including what the
  tasks submitted while running, and their ``then`` / ``on_error`` -- has
  finished. It does not run the Qt event loop: an answer ``then`` hands to
  the GUI thread is delivered when the test processes the events.
- ``TaskExecutor(stepped=True)``: job-lane tasks are queued and run only by
  ``run_next()``, one at a time and in submission order (``newest=True``:
  the last one first), each on a thread of its own; ``queued()`` counts
  them; the fan-out lane runs as usual. One schedule, the same on every
  run. One at a time means it: a step that outlived its ``run_next()``
  (TimeoutError) is waited for before the next one starts.

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
    # map_fanout's cap on the whole wait, in timeouts (each call's own
    # deadline excludes its queue time; this bounds the queue time).
    FANOUT_CAP_FACTOR = 3

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
        # (future, runner), oldest first; the thread of the last step it
        # started (None before the first), which may still be running after
        # its run_next() timed out; and the lock that has run_next() calls
        # from several threads take their turn.
        self._steps = collections.deque()
        self._stepThread = None
        self._stepLock = threading.Lock()
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
        lane, wait for them and return their results in the same order.

        ``timeout`` is each call's own, counted from when the call starts
        running: the lane is shared, and the time a call waits queued
        behind other fetches' requests is not the call's (it was, until the
        S3f-3 review, and a busy lane used up a fetch's deadline before its
        requests ran). The whole wait is capped at ``FANOUT_CAP_FACTOR``
        timeouts, queue time included, so calls the lane never gets to do
        not hold the caller for ever. None: no deadline and no cap.

        A call that raised (logged once, by the lane), was cancelled (the
        executor shut down: the wait sees that at once), ran past its own
        deadline (abandoned: its result is dropped) or had not started by
        the cap (cancelled) is ``default`` in the list.
        """
        startedAt = [None] * len(calls)

        def run(index, call):
            with self._cond:
                startedAt[index] = _clock()
                self._cond.notify_all()
            return call()

        futures = [self.submit_fanout(run, index, call,
                                      task_name=f"{task_name}[{index}]")
                   for index, call in enumerate(calls)]
        cap = None if timeout is None else timeout * self.FANOUT_CAP_FACTOR
        # Waited for on the executor's condition, which every start (run)
        # and every settle (_Settled, a cancel included) notifies -- not
        # with concurrent.futures.wait() (see the module docstring).
        with self._cond:
            capAt = None if cap is None else _clock() + cap
            while True:
                now = _clock()
                # Still worth waiting for: not done, and not started yet or
                # within its own deadline.
                live = [index for index, future in enumerate(futures)
                        if not future.done() and (
                            timeout is None or startedAt[index] is None
                            or now < startedAt[index] + timeout)]
                if not live or (capAt is not None and now >= capAt):
                    break
                wakeAt = None if capAt is None else min(
                    [capAt] + [startedAt[index] + timeout for index in live
                               if startedAt[index] is not None])
                self._cond.wait(None if wakeAt is None else wakeAt - now)
            ranOver = sum(1 for index, future in enumerate(futures)
                          if not future.done()
                          and startedAt[index] is not None)
            notStarted = [future for index, future in enumerate(futures)
                          if not future.done() and startedAt[index] is None]
        # Outside the condition: a cancel runs the done callbacks.
        for future in notStarted:
            future.cancel()
        if ranOver or notStarted:
            logger.warning(
                f"TaskExecutor: {FANOUT} '{task_name}': "
                f"{ranOver + len(notStarted)} of {len(futures)} did not "
                f"finish: {ranOver} ran past {timeout} s, {len(notStarted)} "
                f"did not start within {cap} s")
        results = []
        for future in futures:
            if not future.done() or future.cancelled() \
                    or future.exception() is not None:
                results.append(default)
            else:
                results.append(future.result())
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

    def run_next(self, timeout=30.0, *, newest=False):
        """Stepped mode: run the oldest queued job on a thread of its own and
        wait for it to finish (``timeout`` seconds; None: for ever). False
        when no job is queued or the executor is shut down; TimeoutError if
        it did not finish in time. ``newest``: the newest queued job instead
        (a test making a later answer overtake an earlier one).

        The steps never overlap: a step still running from a run_next() that
        timed out is waited for first (within the same ``timeout``), and if
        it is still running then, TimeoutError is raised again without
        starting another -- a caller that retries (drain_tasks, pumping the
        GUI thread between tries) gets the steps one at a time, as the
        stepped schedule promises."""
        if not self._stepped:
            raise RuntimeError("run_next() is for a stepped TaskExecutor")
        if self._Lane() is not None:
            raise NestedSubmitError("run_next() from a lane thread")
        deadline = _Deadline(timeout)
        if not self._stepLock.acquire(
                timeout=-1 if deadline is None else _Remaining(deadline)):
            raise TimeoutError(
                f"TaskExecutor: another run_next() kept the {JOB} steps "
                f"for {timeout} s")
        try:
            return self._RunNextStep(deadline, timeout, newest)
        finally:
            self._stepLock.release()

    def _RunNextStep(self, deadline, timeout, newest):
        # run_next(), holding _stepLock.
        previous = self._stepThread
        if previous is not None and previous.is_alive():
            previous.join(_Remaining(deadline))
            if previous.is_alive():
                raise TimeoutError(
                    f"TaskExecutor: the previous {JOB} step is still "
                    f"running after {timeout} s")
        with self._cond:
            while True:
                # Checked under the condition shutdown() closes under: a job
                # is either claimed here (running, so the shutdown waits for
                # it, as for a pool thread's task) or cancelled there --
                # never run after the shutdown took the queue.
                if self._closed or not self._steps:
                    return False
                future, runner = (self._steps.pop() if newest
                                  else self._steps.popleft())
                taskName = self._pending.get(future)
                # False: cancelled while queued (it has settled); next one.
                if future.set_running_or_notify_cancel():
                    break
        thread = threading.Thread(
            target=self._RunStep, args=(future, runner),
            name=f"{JOB_THREAD_PREFIX}Step", daemon=True)
        self._stepThread = thread
        thread.start()
        thread.join(_Remaining(deadline))
        if thread.is_alive():
            raise TimeoutError(
                f"TaskExecutor: {JOB} '{taskName}' did not finish within "
                f"{timeout} s")
        return True

    def queued(self):
        """Stepped mode: how many jobs wait for ``run_next()`` (one cancelled
        while queued included, until a step skips it)."""
        if not self._stepped:
            raise RuntimeError("queued() is for a stepped TaskExecutor")
        with self._cond:
            return len(self._steps)

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


def _clock():
    # map_fanout's clock: the starts and the deadlines it compares. A test
    # seam (tests/test_task_executor.py moves it by hand).
    return time.monotonic()
