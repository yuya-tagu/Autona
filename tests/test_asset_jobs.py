"""The game assets and the API window's requests as TaskExecutor work
(ADR-005 S3f-4).

Until S3f-4 the asset adapter (AutonaGameAssetManager) ran its scan, load
and start.gg download on QThreads of its own and decoded its icons on a
QThreadPool, and the API window sent its requests as a Worker on another;
src/Workers.py was their last user. Pinned here:

- nothing in src/ has a Worker, a QRunnable or a pool any more, and
  Workers.py is gone; the adapter and the API window start no thread;
- GuiExecutor.run_sync gives up on a call the GUI thread has not started
  once the app is shutting down or the event loop has exited (the call then
  never runs), and still waits for one already running; a job blocked in
  it no longer holds up the interpreter's exit (subprocess);
- a load is a job: the selection on a job-lane thread, the model rebuild
  and its announcement (gameLoaded, then onLoad) on the GUI thread, once
  each, and the load is over (done()) only after its icon decodes, which
  run on the fan-out lane -- also those a rebuild started before it failed
  part-way; a synchronous load from the GUI thread is refused (every load
  is the job);
- the latest request wins, on one loader job (S3f-4 reviews): rapid A, B,
  C take one job lane and end on C, A and B selecting nothing; a superseded
  synchronous load answers once the newer one is in; a request the loader
  cannot be started for records nothing; a request's Future cannot be
  cancelled, and its generation is finished even when settling it raises;
  a loader that dies between loads fails the request it left and the next
  request starts another; a reload an onLoad slot asks for is served by the
  loader running; an empty codename is SKIPPED at once;
- a load waiting on the GUI thread is released by the shutdown, with
  nothing rebuilt or announced afterwards (its selection published with
  what goes with it: tests/test_roster_flow.py, also for a failed model
  rebuild); one still waiting for the loader -- desktop -- or the lock --
  headless -- writes nothing at all, nor does one whose loader the
  shutdown cancelled while it was queued;
- the game change's full state goes out after the load's delta, with the
  revision of its content (the save cycle itself, one with the roster and
  published on the GUI thread: tests/test_roster_flow.py);
- set_game no longer hops to the GUI thread: the request thread waits for
  the load, and answers once what follows it has run -- on both shells --
  ("OK" also for a failed load, as before),
  and returns instead of hanging once the app is shutting down; the other
  requests that wait for the GUI thread answer 503 then, logged at INFO;
- the start.gg character download is a job and has a timeout;
- the API window's request is a job whose answer lands on the GUI thread,
  and on no window that has been destroyed meanwhile.
"""
import ast
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from qtpy.QtWidgets import QApplication

from src.AutonaGameAssetManager import (
    FAILED, LOADED, SKIPPED, STOPPED, SUPERSEDED)
from src.core.TaskExecutor import (
    FANOUT_THREAD_PREFIX, JOB_THREAD_PREFIX, TaskExecutor)
from src.Helpers import AutonaQtHelper
from src.Helpers.AutonaQtHelper import GuiMarshalAborted
from tests.conftest import drain_tasks
from tests.test_game_asset_manager_models import (  # noqa: F401  (fixtures)
    goldenGames, onLoadCount, statusCalls)

PROJECT_ROOT = Path(__file__).resolve().parents[1]

WAIT = 10  # seconds; what a test waits at most for something that must happen


def _main():
    return threading.main_thread().name


def _pumpUntil(done, timeout=WAIT):
    """Process this (the GUI) thread's events until `done()`."""
    deadline = time.monotonic() + timeout
    while not done():
        assert time.monotonic() < deadline, "timed out"
        QApplication.processEvents()
        time.sleep(0.002)
    QApplication.processEvents()


def _waitFor(condition, timeout=WAIT):
    """Wait, without processing events, until `condition()`."""
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.005)


def _onAnotherThread(fn):
    """Start `fn` on a thread of its own; the thread and its outcome."""
    outcome = {}

    def run():
        try:
            outcome["result"] = fn()
        except BaseException as error:
            outcome["error"] = error

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, outcome


@pytest.fixture
def ownExecutor(autonaApp, monkeypatch):
    """A TaskExecutor of the test's own as TaskExecutor.instance (the
    session's is drained first); shut down afterwards."""
    drain_tasks()
    executor = TaskExecutor()
    monkeypatch.setattr(TaskExecutor, "instance", executor)
    try:
        yield executor
    finally:
        executor.shutdown(WAIT)


# ── no Worker, no pool ───────────────────────────────────────────────────

FORBIDDEN = {"Worker", "Workers", "WorkerSignals", "QRunnable",
             "QThreadPool", "wait_for_all", "waitForDone", "threadpool",
             "threadPool"}


def _names(path):
    """Every name, attribute and imported name or module part in `path`."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    used = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            used.add(node.id)
        elif isinstance(node, ast.Attribute):
            used.add(node.attr)
        elif isinstance(node, ast.ImportFrom):
            used.update((node.module or "").split("."))
            used.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                used.update(alias.name.split("."))
    return used


def test_nothing_in_src_has_a_worker_or_a_pool():
    assert not (PROJECT_ROOT / "src" / "Workers.py").exists()
    found = {path.relative_to(PROJECT_ROOT).as_posix():
             sorted(_names(path) & FORBIDDEN)
             for path in sorted((PROJECT_ROOT / "src").rglob("*.py"))}
    assert {path: names for path, names in found.items() if names} == {}


@pytest.mark.parametrize("path", ["src/AutonaGameAssetManager.py",
                                  "src/AutonaApiInfoWindow.py"])
def test_the_asset_manager_and_the_api_window_start_no_thread(path):
    names = _names(PROJECT_ROOT / path)
    assert not names & {"QThread", "Thread", "start_new_thread"}


def test_set_game_does_not_hop_to_the_gui_thread():
    tree = ast.parse((PROJECT_ROOT / "src" / "AutonaWebOperations.py")
                     .read_text(encoding="utf-8"))
    setGame = next(node for node in tree.body
                   if isinstance(node, ast.FunctionDef)
                   and node.name == "set_game")
    assert setGame.decorator_list == []


# ── run_sync gives up on a call that will not run ────────────────────────


def _blockedMarshal(fn):
    """run_sync(fn) from another thread, this (the GUI) thread not pumping:
    the call is posted and waited for. The thread and its outcome."""
    thread, outcome = _onAnotherThread(
        lambda: AutonaQtHelper.gui_executor.run_sync(fn))
    # run_sync posts at once, then polls: a few polls in, it is waiting.
    time.sleep(4 * AutonaQtHelper.MARSHAL_POLL_SECS)
    return thread, outcome


def test_run_sync_gives_up_once_the_app_is_shutting_down(ownExecutor):
    ran = []
    thread, outcome = _blockedMarshal(lambda: ran.append(1))
    assert thread.is_alive()  # waiting: this thread does not pump

    started = time.monotonic()
    assert ownExecutor.shutdown(WAIT)
    thread.join(WAIT)

    assert not thread.is_alive()
    assert time.monotonic() - started < 2
    assert isinstance(outcome.get("error"), GuiMarshalAborted)
    # The abandoned call never runs, even once the GUI thread gets to it.
    QApplication.processEvents()
    QApplication.processEvents()
    assert ran == []


def test_run_sync_gives_up_once_the_event_loop_has_exited(
        autonaApp, monkeypatch):
    ran = []
    exited = threading.Event()
    monkeypatch.setattr(AutonaQtHelper, "_loopExited", exited)
    thread, outcome = _blockedMarshal(lambda: ran.append(1))
    assert thread.is_alive()

    exited.set()  # what aboutToQuit does
    thread.join(WAIT)

    assert not thread.is_alive()
    assert isinstance(outcome.get("error"), GuiMarshalAborted)
    QApplication.processEvents()
    assert ran == []


def test_run_sync_still_waits_for_a_call_already_running(ownExecutor):
    """The shutdown starts while the GUI thread is running the call: it is
    finished and its answer returned, not abandoned halfway."""
    def call():
        ownExecutor.stopping.set()
        # Several of the waiter's polls see the shutdown meanwhile.
        time.sleep(5 * AutonaQtHelper.MARSHAL_POLL_SECS)
        return threading.current_thread().name

    thread, outcome = _onAnotherThread(
        lambda: AutonaQtHelper.gui_executor.run_sync(call))
    _pumpUntil(lambda: not thread.is_alive())

    assert outcome == {"result": _main()}


def test_run_sync_on_the_gui_thread_runs_the_call_even_when_stopping(
        ownExecutor):
    ownExecutor.shutdown(WAIT)
    assert AutonaQtHelper.gui_executor.run_sync(lambda: 42) == 42


INTERPRETER_EXIT = r"""
import threading
from qtpy.QtCore import QCoreApplication, QTimer
from src.core.TaskExecutor import TaskExecutor
from src.Helpers import AutonaQtHelper
from src.Helpers.AutonaQtHelper import GuiMarshalAborted

app = QCoreApplication([])
AutonaQtHelper.init_gui_executor()
TaskExecutor.instance = TaskExecutor()
entered = threading.Event()

def job():
    entered.set()
    try:
        AutonaQtHelper.gui_executor.run_sync(lambda: print("ran", flush=True))
    except GuiMarshalAborted:
        print("aborted", flush=True)
        return
    print("returned", flush=True)

QTimer.singleShot(0, app.quit)
app.exec()
# The event loop is over (as when the window has closed): a job marshalling
# onto it now gives up...
TaskExecutor.instance.submit(job, task_name="a marshal after the loop")
assert entered.wait(10)
print("exiting", flush=True)
# ...so the interpreter's exit, which joins the job's thread, does not hang.
"""


def test_a_job_blocked_in_the_marshal_does_not_hold_up_the_exit(tmp_path):
    env = dict(os.environ)
    env["QT_API"] = "pyside6"
    env["PYTHONPATH"] = str(PROJECT_ROOT)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")

    result = subprocess.run(
        [sys.executable, "-B", "-c", INTERPRETER_EXIT], cwd=str(tmp_path),
        env=env, capture_output=True, text=True, timeout=60)

    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["exiting", "aborted"] \
        or result.stdout.split() == ["aborted", "exiting"], result.stdout


# ── the load job ─────────────────────────────────────────────────────────


def _record(signal, events, tag):
    def slot(*_args):
        events.append((tag, threading.current_thread().name))
    signal.connect(slot)
    return lambda: signal.disconnect(slot)


def test_a_load_selects_on_a_job_and_announces_on_the_gui_thread(
        goldenGames, autonaApp, monkeypatch):
    manager = goldenGames
    events = []
    decodes = []

    realSelect = manager.catalog.SelectGame

    def select(*args, **kwargs):
        events.append(("select", threading.current_thread().name))
        return realSelect(*args, **kwargs)

    realRebuild = manager._RebuildModels

    def rebuild(started=None):
        events.append(("rebuild", threading.current_thread().name))
        started = realRebuild(started)
        decodes.extend(started)
        return started

    def done():
        events.append(("done", threading.current_thread().name))
        events.append(("decoded", all(f.done() for f in decodes)))

    monkeypatch.setattr(manager.catalog, "SelectGame", select)
    monkeypatch.setattr(manager, "_RebuildModels", rebuild)
    undo = [_record(manager.signals.gameLoaded, events, "gameLoaded"),
            _record(manager.signals.onLoad, events, "onLoad")]
    try:
        future = manager.LoadGameByCodename("tgame", done=done)
        drain_tasks()
        assert future.result(0) == LOADED
    finally:
        for disconnect in undo:
            disconnect()

    assert [event[0] for event in events] == [
        "select", "rebuild", "gameLoaded", "onLoad", "done", "decoded"]
    threads = dict(events)
    assert threads["select"].startswith(JOB_THREAD_PREFIX)
    assert threads["done"] == threads["select"]
    assert threads["rebuild"] == threads["gameLoaded"] == threads["onLoad"] \
        == _main()
    # Over only once its icon decodes were.
    assert decodes and threads["decoded"] is True
    assert autonaApp.get("game.codename") == "tgame"


def test_icon_decodes_run_on_the_fan_out_lane(goldenGames, monkeypatch):
    manager = goldenGames
    threads = []
    realDecode = manager.LoadSkinImages

    def decode(*args):
        threads.append(threading.current_thread().name)
        return realDecode(*args)

    monkeypatch.setattr(manager, "LoadSkinImages", decode)
    manager.LoadGameByCodename("tgame")
    drain_tasks()

    assert threads
    assert all(name.startswith(FANOUT_THREAD_PREFIX) for name in threads)


def test_a_load_job_waiting_on_the_gui_thread_is_released_by_the_shutdown(
        goldenGames, ownExecutor, autonaApp, statusCalls, onLoadCount,
        monkeypatch):
    """The desktop's close: the window shuts the executor down on the GUI
    thread while a load job waits there for its models. The job gives up;
    nothing is rebuilt or announced, not even once the GUI thread runs
    again. Its selection is in the state and published with its cycle, so
    the pick that goes with it is persisted too (from the job's thread)."""
    from src.core.SettingsManager import SettingsManager
    from src.core.StateManager import StateManager

    manager = goldenGames
    generation = manager._modelGeneration
    selected = threading.Event()
    realSelect = manager.catalog.SelectGame

    def select(*args, **kwargs):
        try:
            return realSelect(*args, **kwargs)
        finally:
            selected.set()

    monkeypatch.setattr(manager.catalog, "SelectGame", select)
    done = []
    future = manager.LoadGameByCodename(
        "tgame", done=lambda: done.append(threading.current_thread().name))
    assert selected.wait(WAIT)
    time.sleep(0.2)  # the job is in the marshal now, waiting for us

    started = time.monotonic()
    assert ownExecutor.shutdown(WAIT)

    assert time.monotonic() - started < 2
    assert future.result(0) == STOPPED
    assert len(done) == 1 and done[0].startswith(JOB_THREAD_PREFIX)
    assert StateManager.saveBlocked == 0
    assert StateManager.GetCommitted("game.codename") == "tgame"
    assert SettingsManager.Get("SELECTED_GAME_CODENAME") == "tgame"
    tokens = [token for kind, token, message in statusCalls
              if kind == "push" and "(tgame)" in message]
    assert tokens and all(("pop", token, None) in statusCalls
                          for token in tokens)
    QApplication.processEvents()
    QApplication.processEvents()
    assert manager._modelGeneration == generation
    assert onLoadCount == []


def test_a_load_after_the_shutdown_is_over_at_once(goldenGames, ownExecutor):
    ownExecutor.shutdown(WAIT)
    done = []

    future = goldenGames.LoadGameByCodename("tgame", done=lambda: done.append(1))
    # A synchronous one off the GUI thread (set_game's) does not wait.
    thread, outcome = _onAnotherThread(lambda: goldenGames.LoadGameByCodename(
        "tgame", async_mode=False, done=lambda: done.append(2)))
    thread.join(WAIT)

    assert future.result(0) == STOPPED
    assert not thread.is_alive() and outcome["result"].result(0) == STOPPED
    assert done == [1, 2]


def test_a_synchronous_load_on_the_gui_thread_is_refused(goldenGames):
    """It would wait for a job that marshals onto the waiting thread. The
    GUI thread's inline load it used to make (the tests' only caller) is
    gone: every load is the job."""
    generation = goldenGames._loadGeneration

    with pytest.raises(RuntimeError):
        goldenGames.LoadGameByCodename("tgame", async_mode=False)

    assert goldenGames._loadGeneration == generation  # nothing asked for


# ── the latest request wins, on one loader (ADR-005 S3f-4 review) ───────


def _selections(catalog, monkeypatch):
    """The catalog's SelectGame calls, as (codename, modsActive, reload)."""
    calls = []
    real = catalog.SelectGame

    def select(codename, **kwargs):
        calls.append((codename, kwargs.get("modsActive"),
                      kwargs.get("reload")))
        return real(codename, **kwargs)

    monkeypatch.setattr(catalog, "SelectGame", select)
    return calls


def _holdTheLoader(catalog, monkeypatch):
    """The next load stops at its start -- the start.gg table re-read,
    before it selects -- until the returned `release` is set: "a load is
    running". (`entered`, `release`)."""
    entered, release = threading.Event(), threading.Event()
    real = catalog.ReloadStartGGCharacters

    def reload():
        if not entered.is_set():
            entered.set()
            release.wait(WAIT)
        return real()

    monkeypatch.setattr(catalog, "ReloadStartGGCharacters", reload)
    return entered, release


def _jobsSubmitted(monkeypatch):
    """The task names of the jobs submitted from now on."""
    names = []
    executor = TaskExecutor.instance
    real = executor.submit

    def submit(fn, *args, task_name, **kwargs):
        names.append(task_name)
        return real(fn, *args, task_name=task_name, **kwargs)

    monkeypatch.setattr(executor, "submit", submit)
    return names


def test_rapid_requests_take_one_job_lane_and_end_on_the_last_one(
        goldenGames, autonaApp, monkeypatch):
    """A, B, C asked for while a load runs: none gets a job of its own --
    the loader running takes the latest once it is done -- so a burst of
    switches takes one job lane, not one each from the provider's fetches.
    Only C loads; A and B select nothing and are over (done()) as soon as
    a newer request replaces them."""
    from src.core.StateManager import StateManager

    manager = goldenGames
    entered, release = _holdTheLoader(manager.catalog, monkeypatch)
    jobs = _jobsSubmitted(monkeypatch)
    selections = _selections(manager.catalog, monkeypatch)
    done = []
    running = manager.LoadGameByCodename(
        "game2", done=lambda: done.append("running"))
    assert entered.wait(WAIT)
    requests = [("tgame", False), ("game2", False), ("tgame", True)]
    futures = [manager.LoadGameByCodename(
        codename, mods_active=mods, mods_reload_mode=True,
        done=lambda n=n: done.append(n))
        for n, (codename, mods) in enumerate(requests)]
    assert [future.result(0) for future in futures[:2]] == [
        SUPERSEDED, SUPERSEDED]
    assert done == [0, 1]
    release.set()
    drain_tasks()

    assert jobs == ["game load"]
    assert running.result(0) == LOADED and futures[2].result(0) == LOADED
    assert selections == [("game2", False, False), ("tgame", True, True)]
    assert done == [0, 1, "running", 2]
    assert StateManager.Get("game.codename") == "tgame"
    assert StateManager.Get("game.mods_active") is True
    assert not manager._loaderRunning


def test_a_superseded_synchronous_load_answers_once_the_newer_one_is_in(
        goldenGames, autonaApp, monkeypatch):
    """set_game's wait, overtaken by a newer request before the loader got
    to it: it does not answer when it is superseded, but once the newer
    load is over -- the game asked for after it in the state, what follows
    the load run. It waits on a condition (no polling)."""
    from src.core.StateManager import StateManager

    manager = goldenGames
    manager.LoadGameByCodename("tgame")
    drain_tasks()
    entered, release = _holdTheLoader(manager.catalog, monkeypatch)
    selections = _selections(manager.catalog, monkeypatch)
    events = []
    undo = _record(manager.signals.onLoad, events, "onLoad")

    def request():
        manager.LoadGameByCodename("game2", async_mode=False)
        return list(events), StateManager.Get("game.codename")

    try:
        # The same game again, without reload: skipped once released.
        running = manager.LoadGameByCodename("tgame")
        assert entered.wait(WAIT)
        generation = manager._loadGeneration
        thread, outcome = _onAnotherThread(request)
        deadline = time.monotonic() + WAIT
        while manager._loadGeneration == generation:
            assert time.monotonic() < deadline
            time.sleep(0.005)
        newer = manager.LoadGameByCodename(
            "tgame", mods_active=True, mods_reload_mode=True)
        time.sleep(0.1)
        assert thread.is_alive()  # superseded, still waiting
        release.set()
        _pumpUntil(lambda: not thread.is_alive(), timeout=30)
    finally:
        undo()

    seen, codename = outcome["result"]
    assert selections == [("tgame", False, False), ("tgame", True, True)]
    assert seen == [("onLoad", _main())]
    assert codename == "tgame"
    assert running.result(0) == SKIPPED and newer.result(0) == LOADED


def test_a_request_the_loader_cannot_start_for_records_nothing(
        goldenGames, autonaApp, monkeypatch):
    """The executor refuses the loader's job (NestedSubmitError: a request
    made on one of its lanes): the request raises and leaves nothing
    behind -- no generation that a synchronous request would then wait
    for, for ever (ADR-005 S3f-4 review) -- and the next request starts the
    loader."""
    from src.core.TaskExecutor import NestedSubmitError

    manager = goldenGames
    generation = manager._loadGeneration
    executor = TaskExecutor.instance
    real = executor.submit
    refused = []

    def refuseOnce(fn, *args, task_name, **kwargs):
        if not refused:
            refused.append(task_name)
            raise NestedSubmitError(f"'{task_name}': refused")
        return real(fn, *args, task_name=task_name, **kwargs)

    monkeypatch.setattr(executor, "submit", refuseOnce)
    done = []
    with pytest.raises(NestedSubmitError):
        manager.LoadGameByCodename("tgame", done=lambda: done.append(1))

    assert refused == ["game load"] and done == []
    assert manager._loadGeneration == generation
    assert manager._pendingLoad is None and not manager._loaderRunning

    thread, outcome = _onAnotherThread(
        lambda: manager.LoadGameByCodename("tgame", async_mode=False))
    _pumpUntil(lambda: not thread.is_alive(), timeout=30)
    assert outcome["result"].result(0) == LOADED
    assert manager._loadGeneration == generation + 1


def _waitingRequest(manager, codename="tgame", done=None):
    """A synchronous request (set_game's) made on another thread while a
    load runs, recorded and waiting for the loader; (thread, outcome)."""
    generation = manager._loadGeneration
    thread, outcome = _onAnotherThread(lambda: manager.LoadGameByCodename(
        codename, async_mode=False, done=done))
    _waitFor(lambda: manager._loadGeneration > generation)
    return thread, outcome


def test_a_caller_cannot_cancel_a_load_request(
        goldenGames, autonaApp, monkeypatch):
    """The Future a request returns is the attempt's answer, not a handle on
    it: cancel() is False whether the request is running, waiting for the
    loader, superseded or over. Cancelled, it made the loader's settle
    raise before the generation was finished, and the synchronous request
    it had superseded waited for ever (ADR-005 S3f-4 review)."""
    manager = goldenGames
    entered, release = _holdTheLoader(manager.catalog, monkeypatch)
    done = []
    running = manager.LoadGameByCodename(
        "game2", done=lambda: done.append("running"))
    assert entered.wait(WAIT)
    thread, outcome = _waitingRequest(
        manager, done=lambda: done.append("sync"))
    newer = manager.LoadGameByCodename(
        "tgame", mods_reload_mode=True, done=lambda: done.append("newer"))
    assert not running.cancel() and not newer.cancel()
    release.set()
    _pumpUntil(lambda: not thread.is_alive(), timeout=30)

    superseded = outcome["result"]
    assert not superseded.cancel() and superseded.result(0) == SUPERSEDED
    assert running.result(0) == LOADED and newer.result(0) == LOADED
    assert not newer.cancel()
    assert done == ["sync", "running", "newer"]


def test_a_settle_that_raises_still_finishes_its_generation(
        goldenGames, autonaApp, monkeypatch):
    """The loader finishes a request's generation whatever settling it did
    -- here the settle raises once the newer request's answer is in: the
    synchronous request that newer one superseded returns all the same, the
    loader is over (its lane logs the failure) and the next request starts
    another."""
    manager = goldenGames
    entered, release = _holdTheLoader(manager.catalog, monkeypatch)
    jobs = _jobsSubmitted(monkeypatch)
    realSettle = manager._Settle
    failed = []

    def settle(request, outcome):
        realSettle(request, outcome)
        if request.reload and not failed:
            failed.append(request.codename)
            raise RuntimeError("settling failed")

    monkeypatch.setattr(manager, "_Settle", settle)
    running = manager.LoadGameByCodename("game2")
    assert entered.wait(WAIT)
    thread, outcome = _waitingRequest(manager)
    newer = manager.LoadGameByCodename("tgame", mods_reload_mode=True)
    release.set()
    _pumpUntil(lambda: not thread.is_alive(), timeout=30)

    assert failed == ["tgame"]
    assert outcome["result"].result(0) == SUPERSEDED
    assert running.result(0) == LOADED and newer.result(0) == LOADED
    _waitFor(lambda: not manager._loaderRunning)
    after = manager.LoadGameByCodename("game2")
    drain_tasks()
    assert after.result(0) == LOADED
    assert jobs == ["game load", "game load"]


def test_a_loader_that_dies_between_loads_fails_the_request_it_left(
        goldenGames, autonaApp, monkeypatch):
    """The loader job dies outside a load (after one, before it takes the
    next): its lane logs it, and the request that was waiting for it is over
    -- FAILED, done() once, its synchronous waiter released with that
    answer -- nothing selected for it, and the next request starts a new
    loader."""
    manager = goldenGames
    entered, release = _holdTheLoader(manager.catalog, monkeypatch)
    jobs = _jobsSubmitted(monkeypatch)
    selections = _selections(manager.catalog, monkeypatch)
    realSettle = manager._Settle

    def settle(request, outcome):
        realSettle(request, outcome)
        if request.codename == "game2":
            raise RuntimeError("the loader died")

    monkeypatch.setattr(manager, "_Settle", settle)
    running = manager.LoadGameByCodename("game2")
    assert entered.wait(WAIT)
    done = []
    thread, outcome = _waitingRequest(
        manager, done=lambda: done.append(1))
    release.set()
    _pumpUntil(lambda: not thread.is_alive(), timeout=30)

    assert running.result(0) == LOADED
    assert outcome["result"].result(0) == FAILED and done == [1]
    assert selections == [("game2", False, False)]
    assert not manager._loaderRunning and manager._pendingLoad is None
    after = manager.LoadGameByCodename("tgame")
    drain_tasks()
    assert after.result(0) == LOADED
    assert jobs == ["game load", "game load"]


def test_a_reload_an_onload_slot_asks_for_is_served_by_the_same_loader(
        goldenGames, autonaApp, monkeypatch):
    """An onLoad slot -- on the GUI thread, in _Publish, while the loader
    waits there for it -- asks for another load: it does not deadlock (the
    loader holds no lock a request takes while it waits), and the loader
    running serves it once this load is over, with no job of its own."""
    manager = goldenGames
    jobs = _jobsSubmitted(monkeypatch)
    selections = _selections(manager.catalog, monkeypatch)
    asked = []

    def reload():
        if not asked:
            asked.append((threading.current_thread().name,
                          manager.LoadGameByCodename(
                              "tgame", mods_reload_mode=True)))

    manager.signals.onLoad.connect(reload)
    try:
        first = manager.LoadGameByCodename("tgame")
        drain_tasks()
    finally:
        manager.signals.onLoad.disconnect(reload)

    [(thread, second)] = asked
    assert thread == _main()
    assert first.result(0) == LOADED and second.result(0) == LOADED
    assert selections == [("tgame", False, False), ("tgame", False, True)]
    assert jobs == ["game load"]
    assert not manager._loaderRunning


def test_an_empty_codename_is_skipped_at_once(goldenGames, autonaApp):
    """'No game' (the tournament URL cleared) asks nothing of the loader:
    an already answered Future (SKIPPED), done() called once, here -- on
    the GUI thread too, synchronous or not."""
    manager = goldenGames
    generation = manager._loadGeneration
    done = []

    futures = [
        manager.LoadGameByCodename("", done=lambda: done.append(1)),
        manager.LoadGameByCodename("", async_mode=False,
                                   done=lambda: done.append(2))]

    assert [future.result(0) for future in futures] == [SKIPPED, SKIPPED]
    assert done == [1, 2]
    assert manager._loadGeneration == generation
    assert not manager._loaderRunning


# ── the shutdown: nothing written by a load that cannot finish ──────────


def test_a_load_waiting_for_the_loader_does_not_run_once_stopping(
        goldenGames, ownExecutor, autonaApp, monkeypatch):
    """The shutdown starts while a load waits for the one running: it writes
    nothing -- its selection would reach state.json without what goes with
    it -- and its done() is called (ADR-005 S3f-4 review)."""
    import copy

    from src.core.SettingsManager import SettingsManager
    from src.core.StateManager import StateManager

    manager = goldenGames
    manager.LoadGameByCodename("tgame")
    drain_tasks()
    entered, release = _holdTheLoader(manager.catalog, monkeypatch)
    selections = _selections(manager.catalog, monkeypatch)
    game = copy.deepcopy(StateManager.Get("game"))
    pick = SettingsManager.Get("SELECTED_GAME_CODENAME", "")
    # The same game again, without reload: skipped, writing nothing.
    running = manager.LoadGameByCodename("tgame")
    assert entered.wait(WAIT)
    done = []
    future = manager.LoadGameByCodename("game2", done=lambda: done.append(1))
    ownExecutor.stopping.set()  # what the shutdown sets
    release.set()

    assert future.result(WAIT) == STOPPED
    assert running.result(0) == SKIPPED
    assert selections == [("tgame", False, False)] and done == [1]
    assert StateManager.Get("game") == game
    assert SettingsManager.Get("SELECTED_GAME_CODENAME", "") == pick
    assert StateManager.saveBlocked == 0


def test_a_loader_the_shutdown_cancels_while_queued_stops_its_request(
        goldenGames, ownExecutor, autonaApp, monkeypatch):
    """Every job lane is busy when a load is asked for, so the loader job is
    queued; the shutdown cancels it. The request it would have taken is
    over -- STOPPED, done() once (on the shutdown's thread), its synchronous
    waiter released with that answer -- nothing was selected, and no loader
    is left running."""
    manager = goldenGames
    selections = _selections(manager.catalog, monkeypatch)
    lanes = threading.Barrier(TaskExecutor.JOB_WORKERS + 1)

    def busy():
        lanes.wait(WAIT)
        ownExecutor.stopping.wait(WAIT)

    for _ in range(TaskExecutor.JOB_WORKERS):
        ownExecutor.submit(busy, task_name="busy")
    lanes.wait(WAIT)  # every job lane runs a busy job now
    done = []
    thread, outcome = _waitingRequest(
        manager, codename="game2",
        done=lambda: done.append(threading.current_thread().name))
    loader = manager._loadFuture
    assert manager._loaderRunning and not loader.running()

    assert ownExecutor.shutdown(WAIT)
    thread.join(WAIT)

    assert not thread.is_alive() and outcome["result"].result(0) == STOPPED
    assert loader.cancelled() and done == [_main()]
    assert not manager._loaderRunning and manager._pendingLoad is None
    assert selections == []


def test_a_headless_load_waiting_for_the_lock_does_not_run_once_stopping(
        goldenGames, ownExecutor, autonaApp, monkeypatch):
    import copy

    from src.core.GameAssetCatalog import GameAssetCatalog
    from src.core.StateManager import StateManager
    from src.headless import HeadlessGames

    games = HeadlessGames(GameAssetCatalog.instance, provider=None)
    selections = _selections(GameAssetCatalog.instance, monkeypatch)
    game = copy.deepcopy(StateManager.Get("game"))
    done = []
    with games._lock:
        thread, outcome = _onAnotherThread(lambda: games.LoadGameByCodename(
            "game2", async_mode=False, done=lambda: done.append(1)))
        time.sleep(0.1)
        ownExecutor.stopping.set()
    thread.join(WAIT)  # this thread does not pump

    assert not thread.is_alive() and "error" not in outcome
    assert selections == [] and done == [1]
    assert StateManager.Get("game") == game


# ── the load's save cycle and what reads it ──────────────────────────────


def test_a_rebuild_that_fails_part_way_still_waits_for_its_decodes(
        goldenGames, autonaApp, monkeypatch):
    """The skin decodes are started, then a model fails: the load is over
    (done()) only once those decodes are, and its save cycle is closed."""
    from src.AutonaGameAssetManager import FAILED
    from src.core.StateManager import StateManager

    manager = goldenGames
    started = []
    realDecodeIcons = manager._DecodeIcons
    realSkins = manager.LoadSkinImages

    def decodeIcons(*args, **kwargs):
        future = realDecodeIcons(*args, **kwargs)
        started.append(future)
        return future

    def slowSkins(*args):
        time.sleep(0.2)
        return realSkins(*args)

    realVariants = manager.UpdateVariantModel
    failures = []

    def failing(snapshot=None):
        if not failures:  # the load's rebuild; not the fixture's teardown
            failures.append(1)
            raise RuntimeError("a model failed")
        return realVariants(snapshot)

    monkeypatch.setattr(manager, "_DecodeIcons", decodeIcons)
    monkeypatch.setattr(manager, "LoadSkinImages", slowSkins)
    monkeypatch.setattr(manager, "UpdateVariantModel", failing)
    seen = []
    future = manager.LoadGameByCodename("tgame", done=lambda: seen.append(
        bool(started) and all(decode.done() for decode in started)))
    drain_tasks()

    assert future.result(0) == FAILED
    assert seen == [True]
    assert StateManager.saveBlocked == 0


@pytest.fixture
def socketSends(monkeypatch):
    """The web server's Socket.IO sends, recorded, with its delta flusher
    taking deltas but stepped by hand (WebServer._flush_pending_deltas)."""
    import copy

    from src.AutonaWebServer import WebServer

    sent = []
    monkeypatch.setattr(WebServer, "_delta_pending", [])
    monkeypatch.setattr(WebServer, "_delta_pending_revision", -1)
    monkeypatch.setattr(WebServer, "_delta_pending_first_revision", -1)
    monkeypatch.setattr(WebServer, "_delta_after", [])
    monkeypatch.setattr(WebServer, "_delta_due", None)
    monkeypatch.setattr(WebServer, "_delta_last_flush_ts", 0.0)
    monkeypatch.setattr(WebServer, "_delta_running", True)
    monkeypatch.setattr(WebServer, "_delta_thread", None)
    monkeypatch.setattr(WebServer, "_delta_owner", None)
    # A token no flusher holds (see test_webserver_delta_debounce).
    monkeypatch.setattr(WebServer, "_delta_generation", object())

    def emit(event, *args, **kwargs):
        sent.append((event, copy.deepcopy(args[0]) if args else None))

    monkeypatch.setattr(WebServer, "ws_emit", emit)
    return sent


def test_a_game_change_sends_the_full_state_after_the_loads_delta(
        goldenGames, autonaApp, socketSends):
    """The window's GameChanged runs on onLoad, inside the load's save
    cycle. Its full state goes out after the cycle is published and after
    the cycle's delta: the R+1 delta first, then a full state whose
    revision is R+1 and whose content is that revision's -- not R's
    revision over R+1's content, ahead of the delta (ADR-005 S3f-4
    review). Then the characters."""
    from src.AutonaWebServer import WebServer
    from src.StateManagerBridge import StateManagerBridge

    manager = goldenGames
    bridge = StateManagerBridge.instance
    # What the desktop wires: the bridge's deltas into the flusher (the
    # server's construction), the game change on onLoad (Window.SetGame ->
    # GameChanged -> ws_game_changed).
    bridge.state_updated.connect(WebServer.on_program_state_update)
    manager.signals.onLoad.connect(WebServer.ws_game_changed)
    try:
        manager.LoadGameByCodename("game2")
        drain_tasks()
        # The flusher's step: the open batch, then what waited for it.
        WebServer._flush_pending_deltas()
    finally:
        manager.signals.onLoad.disconnect(WebServer.ws_game_changed)
        bridge.state_updated.disconnect(WebServer.on_program_state_update)

    kinds = [event for event, _payload in socketSends]
    assert "program_state" in kinds, kinds
    full = kinds.index("program_state")
    deltas = [payload for event, payload in socketSends[:full]
              if event == "program_state_update"]
    assert any(row["path"][0] == "game"
               for delta in deltas for row in delta["delta"]), deltas
    state = socketSends[full][1]
    assert state["revision"] == deltas[-1]["revision"]
    assert state["state"]["game"]["codename"] == "game2"
    assert kinds[full + 1] == "characters"


# ── set_game ─────────────────────────────────────────────────────────────


def test_set_game_answers_from_the_request_thread_once_loaded(
        goldenGames, autonaApp):
    """The legacy route on a request thread (Flask's): "OK" once the game
    is in the state and what follows its load has run on the GUI thread."""
    from src.core.StateManager import StateManager

    manager = goldenGames
    web = autonaApp.web()
    events = []
    undo = [_record(manager.signals.onLoad, events, "onLoad")]

    def request():
        answer = web.set_game({"codename": "game2"})
        return (answer, list(events), StateManager.Get("game.codename"),
                threading.current_thread().name)

    try:
        thread, outcome = _onAnotherThread(request)
        _pumpUntil(lambda: not thread.is_alive(), timeout=30)
    finally:
        for disconnect in undo:
            disconnect()

    answer, before, codename, requestThread = outcome["result"]
    assert answer == "OK"
    assert before == [("onLoad", _main())]
    assert codename == "game2"
    assert requestThread != _main()


def test_set_game_answers_ok_once_a_failed_load_is_over(
        goldenGames, autonaApp, monkeypatch):
    """What it answered when it loaded on the GUI thread (358a9d2d: the
    error logged, then "OK"): "OK" once the attempt is over, whatever its
    outcome -- here a load that fails."""
    from src.core.StateManager import StateManager

    manager = goldenGames

    def broken(*args, **kwargs):
        raise RuntimeError("a broken game")

    monkeypatch.setattr(manager.catalog, "SelectGame", broken)
    web = autonaApp.web()
    thread, outcome = _onAnotherThread(
        lambda: web.set_game({"codename": "game2"}))
    _pumpUntil(lambda: not thread.is_alive(), timeout=30)

    assert outcome == {"result": "OK"}
    assert StateManager.saveBlocked == 0


def test_set_game_returns_once_the_app_is_shutting_down(
        goldenGames, ownExecutor, autonaApp):
    ownExecutor.shutdown(WAIT)
    web = autonaApp.web()

    thread, outcome = _onAnotherThread(
        lambda: web.set_game({"codename": "game2"}))
    thread.join(WAIT)  # this thread does not pump

    assert not thread.is_alive()
    assert "error" not in outcome
    assert autonaApp.get("game.codename") != "game2"


def test_a_headless_sync_load_answers_once_its_follow_up_ran(
        goldenGames, autonaApp):
    """The headless loader on a request thread: what follows the load (the
    pick persisted, gameLoaded) runs on the main thread before it returns,
    as when set_game ran there."""
    from src.core.GameAssetCatalog import GameAssetCatalog
    from src.headless import HeadlessGames

    games = HeadlessGames(GameAssetCatalog.instance, provider=None)
    events = []
    _record(games.signals.gameLoaded, events, "gameLoaded")
    done = []

    def load():
        games.LoadGameByCodename("game2", async_mode=False,
                                 done=lambda: done.append(1))
        return list(events)

    thread, outcome = _onAnotherThread(load)
    _pumpUntil(lambda: not thread.is_alive(), timeout=30)

    assert outcome["result"] == [("gameLoaded", _main())]
    assert done == [1]
    assert autonaApp.get("game.codename") == "game2"


def test_a_headless_sync_load_returns_once_the_loop_has_exited(
        goldenGames, autonaApp, monkeypatch):
    """...without loading: what follows the load could not run on the main
    thread any more."""
    import copy

    from src.core.GameAssetCatalog import GameAssetCatalog
    from src.core.StateManager import StateManager
    from src.headless import HeadlessGames

    game = copy.deepcopy(StateManager.Get("game"))
    exited = threading.Event()
    exited.set()
    monkeypatch.setattr(AutonaQtHelper, "_loopExited", exited)
    games = HeadlessGames(GameAssetCatalog.instance, provider=None)
    events = []
    _record(games.signals.gameLoaded, events, "gameLoaded")
    done = []

    thread, outcome = _onAnotherThread(lambda: games.LoadGameByCodename(
        "game2", async_mode=False, done=lambda: done.append(1)))
    thread.join(WAIT)  # this thread does not pump

    assert not thread.is_alive()
    assert "error" not in outcome and done == [1]
    QApplication.processEvents()
    assert events == []
    assert StateManager.Get("game") == game


# ── what waits for the GUI thread from a request, at shutdown ────────────


def _logRecords():
    from loguru import logger

    records = []
    sink = logger.add(lambda message: records.append(message.record),
                      level="DEBUG")
    return records, lambda: logger.remove(sink)


def test_a_request_that_needs_the_gui_thread_at_shutdown_is_unavailable(
        autonaApp, ownExecutor):
    """The board host's operation, an unbuilt board's build, the two shell
    operations: once the GUI thread will not run them, the request is
    answered as unavailable (503) and logged at INFO -- not a 500 with an
    ERROR traceback (ADR-005 S3f-4 review)."""
    from src.AutonaWebOperations import NotAvailable, SHUTTING_DOWN_MESSAGE

    web = autonaApp.web()
    autonaApp.dropAllBoards()   # board 2 would be built on demand below
    ownExecutor.shutdown(WAIT)
    calls = {
        "a board operation": lambda: web.swap_teams(1),
        "an unbuilt board": lambda: web.get_swap(2),
        "the set selection": lambda: web.open_sets(1),
        "the thumbnail": lambda: web.get_thumbnail(1, "png"),
    }
    records, stop = _logRecords()
    try:
        for name, call in calls.items():
            thread, outcome = _onAnotherThread(call)
            thread.join(WAIT)  # this thread does not pump
            assert not thread.is_alive(), name
            error = outcome.get("error")
            assert isinstance(error, NotAvailable), (name, outcome)
            assert error.message == SHUTTING_DOWN_MESSAGE, name
    finally:
        stop()

    assert [r["message"] for r in records if r["level"].name == "ERROR"] \
        == []
    assert any("not run" in r["message"] for r in records
               if r["level"].name == "INFO")
    assert 2 not in autonaApp.manager.scoreboards


# ── the start.gg character download ──────────────────────────────────────


def test_the_character_download_is_a_job_with_a_timeout(
        goldenGames, monkeypatch):
    import src.core.GameAssetCatalog as catalogModule

    calls = []

    def get(url, **kwargs):
        calls.append((threading.current_thread().name, kwargs))
        raise catalogModule.requests.Timeout("no answer")

    monkeypatch.setattr(catalogModule.requests, "get", get)
    goldenGames.DownloadStartGGCharacters()
    drain_tasks()

    [(thread, kwargs)] = calls
    assert thread.startswith(JOB_THREAD_PREFIX)
    assert kwargs["timeout"] == catalogModule.STARTGG_CHARACTERS_TIMEOUT_SECS


# ── the API window's request ─────────────────────────────────────────────


@pytest.fixture
def apiWindow(autonaApp):
    from src.AutonaApiInfoWindow import AutonaApiInfoWindow

    window = AutonaApiInfoWindow()
    window.UiMounted()
    window.pathField.setText("/health")
    yield window
    drain_tasks()
    from shiboken6 import Shiboken
    if Shiboken.isValid(window):
        window.deleteLater()


def _answering(monkeypatch, answer, gate=None):
    """Stand in for the request: records its thread, then answers (or
    raises) once `gate` is open."""
    from src.AutonaApiInfoWindow import AutonaApiInfoWindow

    calls = []

    def request(method, url, body, headers):
        calls.append(threading.current_thread().name)
        if gate is not None:
            assert gate.wait(WAIT)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(AutonaApiInfoWindow, "_performRequest",
                        staticmethod(request))
    return calls


def test_the_api_window_sends_on_a_job_and_answers_on_the_gui_thread(
        apiWindow, monkeypatch):
    calls = _answering(monkeypatch, "HTTP 200 OK\n\n{}")
    shown = []
    realShow = apiWindow._onResponse
    monkeypatch.setattr(apiWindow, "_onResponse", lambda text: (
        shown.append(threading.current_thread().name), realShow(text)))

    apiWindow._sendRequest()
    assert not apiWindow.sendBtn.isEnabled()
    drain_tasks()

    assert len(calls) == 1 and calls[0].startswith(JOB_THREAD_PREFIX)
    assert shown == [_main()]
    assert apiWindow.responseEdit.toPlainText() == "HTTP 200 OK\n\n{}"
    assert apiWindow.sendBtn.isEnabled()


def test_the_api_window_shows_a_failed_request(apiWindow, monkeypatch):
    _answering(monkeypatch, ConnectionError("refused"))

    apiWindow._sendRequest()
    drain_tasks()

    assert apiWindow.responseEdit.toPlainText().endswith(":\nrefused")
    assert apiWindow.sendBtn.isEnabled()


def test_an_answer_for_a_destroyed_api_window_is_dropped(
        apiWindow, monkeypatch):
    from shiboken6 import Shiboken

    gate = threading.Event()
    _answering(monkeypatch, "HTTP 200 OK\n\n{}", gate)
    shown = []
    monkeypatch.setattr(apiWindow, "_onResponse", shown.append)

    apiWindow._sendRequest()
    Shiboken.delete(apiWindow)  # closed while the request runs
    gate.set()
    drain_tasks()

    assert shown == []


def test_the_api_window_gives_the_button_back_after_the_shutdown(
        apiWindow, ownExecutor, monkeypatch):
    calls = _answering(monkeypatch, "HTTP 200 OK\n\n{}")
    ownExecutor.shutdown(WAIT)

    apiWindow._sendRequest()

    assert calls == []
    assert apiWindow.sendBtn.isEnabled()
    # Through the window's translations (the harness runs in Japanese).
    shuttingDown = QApplication.translate("api", "The app is shutting down")
    assert shuttingDown in apiWindow.responseEdit.toPlainText()
