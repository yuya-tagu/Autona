"""The game assets as TaskExecutor work (ADR-005 S3f-4; the headless
shell's loader, S3f-6; one contract for both loaders, S-final SF-6a).

Until S3f-4 the asset adapter (AutonaGameAssetManager) ran its scan, load
and start.gg download on QThreads of its own and decoded its icons on a
QThreadPool, and the API window sent its requests as a Worker on another;
src/Workers.py was their last user. (The API window's requests were a job
from S3f-4 until ADR-005 S-final SF-4a-2, which made the window a page:
its tester sends from the page, web/src/GuidePages/GuidePages.test.js.)
Pinned here:

- the adapter starts no thread (that nothing in src/
  has a Worker, a QRunnable or a pool, and Workers.py is gone, is
  tests/test_task_executor.py's since S3f-6);
- GuiExecutor.run_sync gives up on a call the GUI thread has not started
  once the app is shutting down or the event loop has exited (the call then
  never runs), and still waits for one already running; a job blocked in
  it no longer holds up the interpreter's exit (subprocess);
- the game loader's contract, on both shells (SF-6a): each check runs on
  the desktop's asset adapter and on the headless loader
  (src/headless.HeadlessGames), through the `shell` fixture; where the two
  differ -- the desktop's Future, the headless log line for a request
  nothing takes, the footer's status, what a load the shutdown stopped
  writes -- is SHELL_DIFFERENCES, each parameter's expected value:
  - a load is a job: the selection on a job-lane thread, what follows it
    (the desktop's _Publish, the headless _OnLoaded) and its announcement
    (gameLoaded, then onLoad / gameChanged) on the main thread, once each,
    and the load is over (done()) after them; a synchronous load from the
    main thread is refused (every load is the job); an empty codename is
    over at once. (Until the Qt model clean-up's PR4 the GUI thread rebuilt
    the desktop's Qt models first, and the load was over only after their
    icon decodes on the fan-out lane.);
  - the latest request wins, on one loader job (S3f-4 reviews): rapid A,
    B, C take one job and end on C, A and B selecting nothing; a superseded
    synchronous load answers once the newer one is in; a request the
    loader cannot be started for records nothing; a request's generation
    is finished even when settling it raises; a loader that dies between
    loads ends the request it left and the next request starts another; a
    reload an announcement slot asks for is served by the loader running;
    the game a tournament names says when it is over;
  - a load waiting on the main thread is released by the shutdown or the
    event loop's end (S3f-4's race), with nothing published or announced
    from there afterwards, and writes what follows its selection itself on
    the loader's thread, under the board host's lock (its selection
    published with what goes with it: tests/test_roster_flow.py); one still
    waiting for the loader writes nothing at all, nor does one whose loader
    the shutdown cancelled while it was queued, nor one asked for after
    the shutdown;
  - a load commits what it wrote on a held board and leaves the rest
    pending; every way a load ends gives the save block back
    (saveBlocked == 0: agent-pitfalls #6);
  - the game change's full state goes out after the load's delta, with the
    revision of its content (the save cycle itself: tests/test_roster_flow
    .py);
  - set_game no longer hops to the GUI thread: the request thread waits
    for the load, and a synchronous load answers once what follows it has
    run ("OK" also for a failed load, as before), and returns instead of
    hanging once the main thread is unavailable;
  (the same loads step by step, serially: tests/test_game_loader_golden.py);
- the desktop's own: a request's Future cannot be cancelled; a publication
  that raises fails the load and leaves nothing open;
- the headless loader's own: the start.gg download, the scan and the loads
  are jobs, with no thread of its own; the shutdown waits for a load, which
  settles before ./out and state.json are flushed;
- the other requests that wait for the GUI thread answer 503 once the app
  is shutting down, logged at INFO;
- the start.gg character download is a job and has a timeout.
"""
import ast
import os
import contextlib
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from qtpy.QtWidgets import QApplication

from src.AutonaGameAssetManager import (
    FAILED, LOADED, SKIPPED, STOPPED, SUPERSEDED)
from src.core.TaskExecutor import JOB_THREAD_PREFIX, TaskExecutor
from src.Helpers import AutonaQtHelper
from src.Helpers.AutonaQtHelper import GuiMarshalAborted
from tests.conftest import drain_tasks
from tests.test_game_asset_manager_load import (  # noqa: F401  (fixtures)
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


# ── no thread of their own ───────────────────────────────────────────────
# (Nothing in src/ has a Worker, a QRunnable or a pool:
# tests/test_task_executor.py, since ADR-005 S3f-6.)


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


def test_the_asset_manager_starts_no_thread():
    names = _names(PROJECT_ROOT / "src/AutonaGameAssetManager.py")
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


# ── the game loader's contract, on both shells (ADR-005 S-final SF-6a) ──
#
# The desktop's asset adapter (AutonaGameAssetManager) and the headless
# loader (src/headless.HeadlessGames) take one design (S3f-4, S3f-6). Each
# test below runs on both, through the `shell` fixture; where the two differ
# today -- by design or by history, until SF-6b puts one loader in core --
# the expected value is the parameter's, written down here.

SHELL_DIFFERENCES = {
    "desktop": {
        # LoadGameByCodename returns a Future with the attempt's outcome
        # (LOADED / SKIPPED / SUPERSEDED / STOPPED / FAILED).
        "answers": True,
        # A request nothing will take (asked for after the shutdown; left
        # by a loader cancelled while queued or dead) is settled with no
        # log line of its own.
        "orphanLogs": None,
        # The footer's progress (core's StatusService, SF-2): pushed on the
        # selection's first progress call, popped once the attempt is over.
        "footerStatus": True,
        # What a load the shutdown stopped after its selection writes on the
        # loader's thread (_SettleUnannounced), in order, with whether the
        # board host's lock is held: the roster, its commit (inside the
        # roster's recording: `written.open`), then the pick.
        "settleCalls": [("roster", True), ("commit", True, True),
                        ("pick", False)],
        # The same parts' names.
        "announced": "onLoad",
        "publish": "_Publish",
        "settle": "_SettleUnannounced",
        "pick": "_PersistSelectedGame",
        "fromStartGG": "SetGameFromStartGGId",
    },
    "headless": {
        # Returns None: nobody reads what a headless load returns.
        "answers": False,
        "orphanLogs": {
            "at submit": "Game load of {codename} not run: the app is "
                         "shutting down",
            "cancelled": "Game load of {codename} not run: the loader was "
                         "cancelled",
            "failed": "Game load of {codename} not run: the loader failed",
        },
        # No footer.
        "footerStatus": False,
        # _SettleStopped: the roster through the board host, no commit (the
        # headless boards are not held), then the pick.
        "settleCalls": [("roster", True), ("pick", False)],
        "announced": "gameChanged",
        "publish": "_OnLoaded",
        "settle": "_SettleStopped",
        "pick": "_PersistPick",
        "fromStartGG": "SelectGameFromStartGGId",
    },
}


class _Shell:
    """One shell's game loader, as the contract tests drive it."""

    def __init__(self, name, games):
        self.name = name
        self.games = games
        self.differs = SHELL_DIFFERENCES[name]

    @property
    def catalog(self):
        return self.games.catalog

    @property
    def announced(self):
        """The announcement after gameLoaded: the desktop's onLoad, the
        headless loader's gameChanged."""
        return getattr(self.games.signals, self.differs["announced"])

    def Load(self, codename, **kwargs):
        return self.games.LoadGameByCodename(codename, **kwargs)

    def FromStartGG(self, gameid, **kwargs):
        return getattr(self.games, self.differs["fromStartGG"])(
            gameid, **kwargs)

    def Answer(self, returned, timeout=0):
        """What a LoadGameByCodename call returned, read: the desktop's
        Future's outcome; None headless (checked)."""
        if self.differs["answers"]:
            return returned.result(timeout)
        assert returned is None, returned
        return None

    def Said(self, outcome):
        """The answer a load that ended so gives on this shell."""
        return outcome if self.differs["answers"] else None

    def Real(self, part):
        return getattr(self.games, self.differs[part])

    def Patch(self, monkeypatch, part, replacement):
        monkeypatch.setattr(self.games, self.differs[part], replacement)

    def PatchSettle(self, monkeypatch, hook):
        """Every request's settle (its done(), and the desktop's Future)
        followed by `hook(request)`."""
        real = self.games._Settle

        def settle(request, *outcome):
            real(request, *outcome)
            hook(request)

        monkeypatch.setattr(self.games, "_Settle", settle)

    def OrphanLogs(self, codename, why):
        """The log lines a request nothing takes leaves (`why`: 'at
        submit', 'cancelled' or 'failed')."""
        logs = self.differs["orphanLogs"]
        return [] if logs is None else [logs[why].format(codename=codename)]


@pytest.fixture(params=["desktop", "headless"])
def shell(request, goldenGames, autonaApp, monkeypatch):
    """Each shell's game loader over the fixture games (goldenGames): the
    desktop's asset adapter as bootstrap_gui() wired it, or a headless
    loader wired as src/headless.Assemble wires its own -- the player DB's
    refresh on gameChanged and the boards' roster on gameLoaded
    (bootstrap.wire_game_roster), through a board host of its own."""
    if request.param == "desktop":
        return _Shell("desktop", goldenGames)
    from src.AutonaPlayerDB import AutonaPlayerDB
    from src.AutonaTournamentDataProvider import AutonaTournamentDataProvider
    from src.bootstrap import wire_game_roster

    games = _headlessGames(
        monkeypatch, provider=AutonaTournamentDataProvider.instance)
    games.signals.gameChanged.connect(AutonaPlayerDB.SetupModel)
    wire_game_roster(games.signals.gameLoaded)
    return _Shell("headless", games)


def _headlessGames(monkeypatch, provider=None):
    """A headless loader over the session's catalog, with a board host of
    its own (what follows a load the shutdown stopped writes through it;
    the one BoardHost of both shells since ADR-005 S4ad -- the harness had
    registered the desktop's until then)."""
    from src import AutonaWebOperations as WebOps
    from src.AutonaWebOperations import BoardHost
    from src.core.GameAssetCatalog import GameAssetCatalog
    from src.headless import HeadlessGames

    monkeypatch.setattr(WebOps, "boardHost", BoardHost())
    return HeadlessGames(GameAssetCatalog.instance, provider=provider)


def _record(signal, events, tag):
    def slot(*_args):
        events.append((tag, threading.current_thread().name))
    signal.connect(slot)
    return lambda: signal.disconnect(slot)


def _recordPublish(shell, monkeypatch, events):
    """The main thread's end of a load -- the desktop's _Publish, the
    headless loader's _OnLoaded -- recorded as it starts (the desktop's
    model rebuild was recorded there until the Qt model clean-up's PR4: it
    was _Publish's first step)."""
    real = shell.Real("publish")

    def publish(*args):
        events.append(("publish", threading.current_thread().name))
        return real(*args)

    shell.Patch(monkeypatch, "publish", publish)


def _marshalEntered(monkeypatch):
    """Set once a thread other than the main one enters
    GuiExecutor.run_sync: a load waits for the main thread from then on."""
    from src.Helpers.AutonaQtHelper import GuiExecutor

    entered = threading.Event()
    real = GuiExecutor.run_sync

    def run_sync(self, fn):
        if threading.current_thread() is not threading.main_thread():
            entered.set()
        return real(self, fn)

    monkeypatch.setattr(GuiExecutor, "run_sync", run_sync)
    return entered


def _notRun(records):
    """The log lines of loads that did not run (_logRecords' records)."""
    return [record["message"] for record in records
            if record["message"].startswith("Game load of ")
            and " not run: " in record["message"]]


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


def _waitingRequest(games, codename="tgame", done=None, **kwargs):
    """A synchronous request (set_game's) made on another thread while a
    load runs, recorded and waiting for the loader; (thread, outcome)."""
    generation = games._loadGeneration
    thread, outcome = _onAnotherThread(lambda: games.LoadGameByCodename(
        codename, async_mode=False, done=done, **kwargs))
    _waitFor(lambda: games._loadGeneration > generation)
    return thread, outcome


def _rosterRuns(monkeypatch):
    """The threads AutonaActions.ApplyGameRoster runs on from now on."""
    from src.core.Actions import AutonaActions

    threads = []
    actions = AutonaActions.instance
    real = actions.ApplyGameRoster

    def roster(*args, **kwargs):
        threads.append(threading.current_thread().name)
        return real(*args, **kwargs)

    monkeypatch.setattr(actions, "ApplyGameRoster", roster)
    return threads


# ── the load job ─────────────────────────────────────────────────────────


def test_a_load_selects_on_a_job_and_announces_on_the_main_thread(
        shell, autonaApp, monkeypatch):
    """The selection on a job-lane thread; what follows it (the desktop's
    _Publish, the headless loader's _OnLoaded) and the announcement
    (gameLoaded, then onLoad / gameChanged) on the main (GUI) thread, once
    each; the load is over (done()) after them, on the loader's thread."""
    events = []
    realSelect = shell.catalog.SelectGame

    def select(*args, **kwargs):
        events.append(("select", threading.current_thread().name))
        return realSelect(*args, **kwargs)

    def done():
        events.append(("done", threading.current_thread().name))

    monkeypatch.setattr(shell.catalog, "SelectGame", select)
    _recordPublish(shell, monkeypatch, events)
    undo = [_record(shell.games.signals.gameLoaded, events, "gameLoaded"),
            _record(shell.announced, events, "announced")]
    try:
        returned = shell.Load("tgame", done=done)
        drain_tasks()
        assert shell.Answer(returned) == shell.Said(LOADED)
    finally:
        for disconnect in undo:
            disconnect()

    assert [event[0] for event in events] == [
        "select", "publish", "gameLoaded", "announced", "done"]
    threads = dict(events)
    assert threads["select"].startswith(JOB_THREAD_PREFIX)
    assert threads["done"] == threads["select"]
    assert threads["publish"] == threads["gameLoaded"] \
        == threads["announced"] == _main()
    assert autonaApp.get("game.codename") == "tgame"


# What releases a load waiting for the main thread: (the game loaded before
# it, the game it loads).
RELEASES = {
    # The desktop's close: the window shuts the executor down on the GUI
    # thread while the load waits there.
    "the shutdown": (None, "tgame"),
    # S3f-4's race (its final review, 2): the event loop ends (aboutToQuit)
    # while the load waits. Until S3f-6 the headless loader checked that
    # the main thread still ran its calls, then queued what follows a load
    # unwaited, and an event loop ending in between lost it -- the
    # selection in the state, the previous pick persisted.
    "the event loop's end": ("tgame", "game2"),
}


@pytest.mark.parametrize("release", list(RELEASES))
def test_a_load_waiting_for_the_main_thread_is_released_and_settles_its_game(
        shell, ownExecutor, autonaApp, statusCalls, monkeypatch, release):
    """The load waits on the claim-based marshal (run_sync) for what follows
    its selection; the shutdown or the event loop's end gives the call up
    and it never runs, not even once the main thread runs again: nothing is
    published or announced from there. The loader writes what follows the
    selection itself, on its own thread -- the roster, the pick -- so the
    state holds one game, published, and the save block is given back."""
    from src.core.SettingsManager import SettingsManager
    from src.core.StateManager import StateManager

    games = shell.games
    before, codename = RELEASES[release]
    if before is not None:
        shell.Load(before)
        drain_tasks()
        assert SettingsManager.Get("SELECTED_GAME_CODENAME") != codename
    exited = threading.Event()
    monkeypatch.setattr(AutonaQtHelper, "_loopExited", exited)
    published, announced = [], []
    _recordPublish(shell, monkeypatch, published)
    undo = [_record(games.signals.gameLoaded, announced, "gameLoaded"),
            _record(shell.announced, announced, "announced")]
    rosters = _rosterRuns(monkeypatch)
    entered = _marshalEntered(monkeypatch)
    statusCalls.clear()
    done = []
    try:
        returned = shell.Load(codename, done=lambda: done.append(
            threading.current_thread().name))
        assert entered.wait(WAIT)
        # In the marshal now, waiting for this thread, which does not pump.
        time.sleep(4 * AutonaQtHelper.MARSHAL_POLL_SECS)
        assert done == []
        started = time.monotonic()
        if release == "the shutdown":
            assert ownExecutor.shutdown(WAIT)
            assert time.monotonic() - started < 2
        else:
            exited.set()  # what aboutToQuit sets
            _waitFor(lambda: done)

        assert shell.Answer(returned, WAIT) == shell.Said(STOPPED)
        assert len(done) == 1 and done[0].startswith(JOB_THREAD_PREFIX)
        assert rosters == done  # the roster, once, on the loader's thread
        assert StateManager.saveBlocked == 0
        assert StateManager.GetCommitted("game.codename") == codename
        assert SettingsManager.Get("SELECTED_GAME_CODENAME") == codename
        tokens = [token for kind, token, message in statusCalls
                  if kind == "push" and f"({codename})" in message]
        if shell.differs["footerStatus"]:
            assert tokens and all(("pop", token, None) in statusCalls
                                  for token in tokens)
        else:
            assert tokens == []
        QApplication.processEvents()
        QApplication.processEvents()
        assert published == [] and announced == []
    finally:
        for disconnect in undo:
            disconnect()
    _waitFor(lambda: not games._loaderRunning)


def test_a_load_after_the_shutdown_is_over_at_once(shell, ownExecutor):
    """Asked for once the executor has shut down, the loader job is
    cancelled as it is submitted: the request is over at once (done()
    here), asynchronous or not -- a synchronous one off the main thread
    (set_game's) does not wait."""
    ownExecutor.shutdown(WAIT)
    done = []
    records, stop = _logRecords()
    try:
        returned = shell.Load("tgame", done=lambda: done.append(1))
        thread, outcome = _onAnotherThread(lambda: shell.Load(
            "tgame", async_mode=False, done=lambda: done.append(2)))
        thread.join(WAIT)
    finally:
        stop()

    assert shell.Answer(returned) == shell.Said(STOPPED)
    assert not thread.is_alive() and "error" not in outcome
    assert shell.Answer(outcome["result"]) == shell.Said(STOPPED)
    assert done == [1, 2]
    assert _notRun(records) == shell.OrphanLogs("tgame", "at submit") * 2
    assert not shell.games._loaderRunning
    assert shell.games._pendingLoad is None


def test_a_synchronous_load_on_the_main_thread_is_refused(shell):
    """It would wait for the loader, which hands what follows the selection
    to the waiting thread: nothing is asked for and nothing is done. (The
    desktop's inline load on the GUI thread, the tests' only caller, is
    gone: every load is the job.)"""
    games = shell.games
    generation = games._loadGeneration
    done = []

    with pytest.raises(RuntimeError):
        shell.Load("game2", async_mode=False, done=lambda: done.append(1))

    assert done == [] and games._loadGeneration == generation
    assert not games._loaderRunning


def test_an_empty_codename_is_over_at_once(shell, autonaApp):
    """'No game' (the tournament URL cleared) asks nothing of the loader:
    done() called once, here -- on the main thread too, synchronous or not
    -- and the desktop's Future answered already (SKIPPED)."""
    games = shell.games
    generation = games._loadGeneration
    done = []

    returned = [shell.Load("", done=lambda: done.append(1)),
                shell.Load("", async_mode=False, done=lambda: done.append(2))]

    assert [shell.Answer(each) for each in returned] == \
        [shell.Said(SKIPPED)] * 2
    assert done == [1, 2]
    assert games._loadGeneration == generation
    assert not games._loaderRunning


# ── the latest request wins, on one loader (ADR-005 S3f-4 review) ───────


def test_rapid_requests_take_one_job_and_end_on_the_last_one(
        shell, autonaApp, monkeypatch):
    """A, B, C asked for while a load runs: none gets a job of its own --
    the loader running takes the latest once it is done -- so a burst of
    switches takes one job lane, not one each from the provider's fetches.
    Only C loads; A and B select nothing and are over (done()) as soon as
    a newer request replaces them. (Until S3f-6 each headless request was a
    thread of its own behind a lock that is not FIFO, so they could end on
    any.)"""
    from src.core.StateManager import StateManager

    games = shell.games
    entered, release = _holdTheLoader(shell.catalog, monkeypatch)
    jobs = _jobsSubmitted(monkeypatch)
    selections = _selections(shell.catalog, monkeypatch)
    done = []
    running = shell.Load("game2", done=lambda: done.append("running"))
    assert entered.wait(WAIT)
    requests = [("tgame", False), ("game2", False), ("tgame", True)]
    returned = [shell.Load(codename, mods_active=mods, mods_reload_mode=True,
                           done=lambda n=n: done.append(n))
                for n, (codename, mods) in enumerate(requests)]
    assert [shell.Answer(each) for each in returned[:2]] == \
        [shell.Said(SUPERSEDED)] * 2
    assert done == [0, 1]
    release.set()
    drain_tasks()

    assert jobs == ["game load"]
    assert shell.Answer(running) == shell.Said(LOADED)
    assert shell.Answer(returned[2]) == shell.Said(LOADED)
    assert selections == [("game2", False, False), ("tgame", True, True)]
    assert done == [0, 1, "running", 2]
    assert StateManager.Get("game.codename") == "tgame"
    assert StateManager.Get("game.mods_active") is True
    assert not games._loaderRunning and games._pendingLoad is None


# The load running when a synchronous request (set_game's) is overtaken:
# (its game, the synchronous request's game). tgame is loaded first, so the
# same game again without reload is skipped once released, and another
# game is loaded.
OVERTAKEN = {
    "the same game again": ("tgame", "game2"),
    "another game": ("game2", "tgame"),
}


@pytest.mark.parametrize("running", list(OVERTAKEN))
def test_a_superseded_synchronous_load_answers_once_the_newer_one_is_in(
        shell, autonaApp, monkeypatch, running):
    """set_game's wait, overtaken by a newer request before the loader got
    to it: it is over (done()) when it is superseded, but does not answer
    then -- it answers once the newer load is over: the game asked for
    after it in the state, what follows the load run on the main thread.
    It waits on a condition (no polling)."""
    from src.core.StateManager import StateManager

    games = shell.games
    runningGame, syncGame = OVERTAKEN[running]
    shell.Load("tgame")
    drain_tasks()
    entered, release = _holdTheLoader(shell.catalog, monkeypatch)
    selections = _selections(shell.catalog, monkeypatch)
    events = []
    undo = [_record(games.signals.gameLoaded, events, "gameLoaded"),
            _record(shell.announced, events, "announced")]
    done = []

    def request():
        returned = shell.Load(syncGame, async_mode=False,
                              done=lambda: done.append("sync"))
        return returned, list(events), StateManager.Get("game.codename")

    try:
        runningLoad = shell.Load(runningGame)
        assert entered.wait(WAIT)
        generation = games._loadGeneration
        thread, outcome = _onAnotherThread(request)
        _waitFor(lambda: games._loadGeneration > generation)
        newer = shell.Load("tgame", mods_active=True, mods_reload_mode=True)
        assert done == ["sync"]  # superseded: over, its wait is not
        time.sleep(0.1)
        assert thread.is_alive()
        release.set()
        _pumpUntil(lambda: not thread.is_alive(), timeout=30)
    finally:
        for disconnect in undo:
            disconnect()

    assert "error" not in outcome, outcome
    superseded, seen, codename = outcome["result"]
    loads = 1 if runningGame == "tgame" else 2
    assert selections == [(runningGame, False, False), ("tgame", True, True)]
    assert seen == [("gameLoaded", _main()), ("announced", _main())] * loads
    assert codename == "tgame"
    assert shell.Answer(superseded) == shell.Said(SUPERSEDED)
    assert shell.Answer(runningLoad) == shell.Said(
        SKIPPED if loads == 1 else LOADED)
    assert shell.Answer(newer) == shell.Said(LOADED)


def test_a_request_the_loader_cannot_start_for_records_nothing(
        shell, autonaApp, monkeypatch):
    """The executor refuses the loader's job (NestedSubmitError: a request
    made on one of its lanes): the request raises and leaves nothing
    behind -- no generation that a synchronous request would then wait
    for, for ever (ADR-005 S3f-4 review) -- and the next request starts the
    loader."""
    from src.core.TaskExecutor import NestedSubmitError

    games = shell.games
    generation = games._loadGeneration
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
        shell.Load("tgame", done=lambda: done.append(1))

    assert refused == ["game load"] and done == []
    assert games._loadGeneration == generation
    assert games._pendingLoad is None and not games._loaderRunning

    thread, outcome = _onAnotherThread(
        lambda: shell.Load("tgame", async_mode=False))
    _pumpUntil(lambda: not thread.is_alive(), timeout=30)
    assert "error" not in outcome, outcome
    assert shell.Answer(outcome["result"]) == shell.Said(LOADED)
    assert games._loadGeneration == generation + 1
    assert autonaApp.get("game.codename") == "tgame"


def test_a_settle_that_raises_still_finishes_its_generation(
        shell, autonaApp, monkeypatch):
    """The loader finishes a request's generation whatever settling it did
    -- here the settle raises once the newer request's answer is in: the
    synchronous request that newer one superseded returns all the same, the
    loader is over (its lane logs the failure) and the next request starts
    another."""
    games = shell.games
    entered, release = _holdTheLoader(shell.catalog, monkeypatch)
    jobs = _jobsSubmitted(monkeypatch)
    failed = []

    def hook(request):
        if request.reload and not failed:
            failed.append(request.codename)
            raise RuntimeError("settling failed")

    shell.PatchSettle(monkeypatch, hook)
    running = shell.Load("game2")
    assert entered.wait(WAIT)
    thread, outcome = _waitingRequest(games)
    newer = shell.Load("tgame", mods_reload_mode=True)
    release.set()
    _pumpUntil(lambda: not thread.is_alive(), timeout=30)

    assert failed == ["tgame"]
    assert "error" not in outcome, outcome
    assert shell.Answer(outcome["result"]) == shell.Said(SUPERSEDED)
    assert shell.Answer(running) == shell.Said(LOADED)
    assert shell.Answer(newer) == shell.Said(LOADED)
    _waitFor(lambda: not games._loaderRunning)
    after = shell.Load("game2")
    drain_tasks()
    assert shell.Answer(after) == shell.Said(LOADED)
    assert jobs == ["game load", "game load"]


def test_a_loader_that_dies_between_loads_ends_the_request_it_left(
        shell, autonaApp, monkeypatch):
    """The loader job dies outside a load (after one, before it takes the
    next): its lane logs it, and the request that was waiting for it is over
    -- done() once, its synchronous waiter released (FAILED on the desktop;
    a log line headless) -- nothing selected for it, and the next request
    starts a new loader."""
    games = shell.games
    entered, release = _holdTheLoader(shell.catalog, monkeypatch)
    jobs = _jobsSubmitted(monkeypatch)
    selections = _selections(shell.catalog, monkeypatch)

    def hook(request):
        if request.codename == "game2":
            raise RuntimeError("the loader died")

    shell.PatchSettle(monkeypatch, hook)
    running = shell.Load("game2")
    assert entered.wait(WAIT)
    done = []
    records, stop = _logRecords()
    try:
        thread, outcome = _waitingRequest(
            games, done=lambda: done.append(1))
        release.set()
        _pumpUntil(lambda: not thread.is_alive(), timeout=30)
    finally:
        stop()

    assert shell.Answer(running) == shell.Said(LOADED)
    assert "error" not in outcome, outcome
    assert shell.Answer(outcome["result"]) == shell.Said(FAILED)
    assert done == [1]
    assert selections == [("game2", False, False)]
    assert _notRun(records) == shell.OrphanLogs("tgame", "failed")
    assert not games._loaderRunning and games._pendingLoad is None
    after = shell.Load("tgame")
    drain_tasks()
    assert shell.Answer(after) == shell.Said(LOADED)
    assert jobs == ["game load", "game load"]


def test_a_reload_an_announcement_slot_asks_for_is_served_by_the_same_loader(
        shell, autonaApp, monkeypatch):
    """An announcement slot (onLoad / gameChanged) -- on the main thread,
    while the loader waits there for it -- asks for another load: it does
    not deadlock (the loader holds no lock a request takes while it waits),
    and the loader running serves it once this load is over, with no job of
    its own."""
    games = shell.games
    jobs = _jobsSubmitted(monkeypatch)
    selections = _selections(shell.catalog, monkeypatch)
    asked = []

    def reload():
        if not asked:
            asked.append((threading.current_thread().name,
                          shell.Load("tgame", mods_reload_mode=True)))

    shell.announced.connect(reload)
    try:
        first = shell.Load("tgame")
        drain_tasks()
    finally:
        shell.announced.disconnect(reload)

    [(thread, second)] = asked
    assert thread == _main()
    assert shell.Answer(first) == shell.Said(LOADED)
    assert shell.Answer(second) == shell.Said(LOADED)
    assert selections == [("tgame", False, False), ("tgame", False, True)]
    assert jobs == ["game load"]
    assert not games._loaderRunning


def test_the_game_a_tournament_names_says_when_it_is_over(
        shell, autonaApp, monkeypatch):
    """The standings' import waits for it (the provider's
    RefreshTournamentData). A game that is not one of ours asks nothing of
    the loader -- nor anything at all with nobody to tell."""
    games = shell.games
    generation = games._loadGeneration
    done = []

    shell.FromStartGG("no such game", done=lambda: done.append(1))
    assert done == [1]
    assert games._loadGeneration == generation

    shell.FromStartGG("no such game")
    assert done == [1]
    assert games._loadGeneration == generation
    assert not games._loaderRunning

    monkeypatch.setattr(shell.catalog, "GameFromStartGGId",
                        lambda _gameid: "tgame")
    shell.FromStartGG(7, done=lambda: done.append(2))
    drain_tasks()
    assert done == [1, 2]
    assert shell.catalog.Current().codename == "tgame"


# ── the shutdown: nothing written by a load that cannot finish ──────────


@pytest.mark.parametrize("waiting", ["asynchronous", "synchronous"])
def test_a_load_waiting_for_the_loader_does_not_run_once_stopping(
        shell, ownExecutor, autonaApp, monkeypatch, waiting):
    """The shutdown starts while a load waits for the one running: it writes
    nothing -- its selection would reach state.json without what goes with
    it -- logs why, and its done() is called and its wait released (ADR-005
    S3f-4 review)."""
    import copy

    from src.core.SettingsManager import SettingsManager
    from src.core.StateManager import StateManager

    games = shell.games
    shell.Load("tgame")
    drain_tasks()
    entered, release = _holdTheLoader(shell.catalog, monkeypatch)
    selections = _selections(shell.catalog, monkeypatch)
    game = copy.deepcopy(StateManager.Get("game"))
    pick = SettingsManager.Get("SELECTED_GAME_CODENAME", "")
    # The same game again, without reload: skipped, writing nothing.
    running = shell.Load("tgame")
    assert entered.wait(WAIT)
    done = []
    records, stop = _logRecords()
    try:
        if waiting == "asynchronous":
            returned = shell.Load("game2", done=lambda: done.append(1))
        else:
            thread, outcome = _waitingRequest(
                games, "game2", done=lambda: done.append(1))
        ownExecutor.stopping.set()  # what the shutdown sets
        release.set()
        if waiting == "asynchronous":
            _waitFor(lambda: done)
        else:
            thread.join(WAIT)  # this thread does not pump
            assert not thread.is_alive() and "error" not in outcome
            returned = outcome["result"]
        assert shell.Answer(returned, WAIT) == shell.Said(STOPPED)
        _waitFor(lambda: not games._loaderRunning)
    finally:
        stop()

    assert shell.Answer(running) == shell.Said(SKIPPED)
    assert selections == [("tgame", False, False)] and done == [1]
    assert StateManager.Get("game") == game
    assert SettingsManager.Get("SELECTED_GAME_CODENAME", "") == pick
    assert StateManager.saveBlocked == 0
    assert _notRun(records) == [
        "Game load of game2 not run: the app is shutting down"]


def test_a_loader_the_shutdown_cancels_while_queued_ends_its_request(
        shell, ownExecutor, autonaApp, monkeypatch):
    """Every job lane is busy when a load is asked for, so the loader job is
    queued; the shutdown cancels it. The request it would have taken is
    over -- done() once (on the shutdown's thread), its synchronous waiter
    released (STOPPED on the desktop; a log line headless) -- nothing was
    selected, and no loader is left running."""
    games = shell.games
    selections = _selections(shell.catalog, monkeypatch)
    lanes = threading.Barrier(TaskExecutor.JOB_WORKERS + 1)

    def busy():
        lanes.wait(WAIT)
        ownExecutor.stopping.wait(WAIT)

    for _ in range(TaskExecutor.JOB_WORKERS):
        ownExecutor.submit(busy, task_name="busy")
    lanes.wait(WAIT)  # every job lane runs a busy job now
    done = []
    thread, outcome = _waitingRequest(
        games, codename="game2",
        done=lambda: done.append(threading.current_thread().name))
    loader = games._loadFuture
    assert games._loaderRunning and not loader.running()
    records, stop = _logRecords()
    try:
        assert ownExecutor.shutdown(WAIT)
        thread.join(WAIT)
    finally:
        stop()

    assert not thread.is_alive() and "error" not in outcome
    assert shell.Answer(outcome["result"]) == shell.Said(STOPPED)
    assert loader.cancelled() and done == [_main()]
    assert not games._loaderRunning and games._pendingLoad is None
    assert selections == []
    assert _notRun(records) == shell.OrphanLogs("game2", "cancelled")


# ── what a load writes, and the save block ──────────────────────────────


def test_a_load_commits_what_it_wrote_and_leaves_the_rest_pending(
        shell, autonaApp):
    """The load's commit (AutonaActions.CommitGameLoad, from the roster's
    wiring) publishes what the load wrote on a held board and nothing else
    (StateManager.RecordHeldWrites; the fix of 2026-11-12): a switch empties
    the previous game's picks -- published, the live and the published value
    alike -- while a team name typed before it and not applied (反映) stays
    pending. Both shells' loads commit (the harness holds the boards, as
    the desktop's launch does)."""
    from src.core.Actions import AutonaActions
    from src.core.PlayerService import CharacterSlot, PlayerService
    from src.core.StateManager import StateManager

    autonaApp.ready(1)
    shell.Load("tgame")
    drain_tasks()
    slot = "score.1.team.1.player.1"
    with StateManager.SaveBlock():
        PlayerService(slot).PublishCharacterSlots(
            [CharacterSlot(character="Mario")], includeMains=True)
    StateManager.CommitHeld("score.1")
    autonaApp.settle()
    assert [value.get("en_name") for value in StateManager.GetCommitted(
        f"{slot}.character").values()] == ["Mario"]
    AutonaActions.instance.SetTeamName(1, 1, "Alpha")
    assert StateManager.HasPendingChanges("score.1")

    shell.Load("game2")
    drain_tasks()
    autonaApp.settle()

    assert StateManager.GetCommitted("game.codename") == "game2"
    for group in ("character", "mains"):
        live = StateManager.Get(f"{slot}.{group}")
        assert StateManager.GetCommitted(f"{slot}.{group}") == live, group
        assert live and all(value.get("name") == "" for value in
                            live.values()), (group, live)
    assert StateManager.Get("score.1.team.1.teamName") == "Alpha"
    assert StateManager.GetCommitted("score.1.team.1.teamName") != "Alpha"
    assert StateManager.HasPendingChanges("score.1")
    assert StateManager.saveBlocked == 0


def _saveBlocks(monkeypatch):
    """StateManager.BlockSaving / ReleaseSaving calls from now on, from any
    thread, counted."""
    from src.core.StateManager import StateManager

    counts = {"block": 0, "release": 0}
    lock = threading.Lock()
    realBlock, realRelease = StateManager.BlockSaving, \
        StateManager.ReleaseSaving

    def block():
        with lock:
            counts["block"] += 1
        return realBlock()

    def release():
        with lock:
            counts["release"] += 1
        return realRelease()

    monkeypatch.setattr(StateManager, "BlockSaving", block)
    monkeypatch.setattr(StateManager, "ReleaseSaving", release)
    return counts


# Each way a load ends: (the outcome its request is answered with on the
# desktop, whether a save block was opened on the way).
SAVE_BLOCK_PATHS = {
    # The same game again without reload: the catalog selects nothing.
    "skipped": (SKIPPED, True),
    "SelectGame raises": (FAILED, True),
    # What follows the selection on the main thread raises (the desktop's
    # _Publish, before its board operation; the headless _OnLoaded).
    "the publication raises": (FAILED, True),
    # The loader takes the request once the event loop has ended.
    "not run: the main thread is gone": (STOPPED, False),
    # The event loop ends while the load waits for the main thread.
    "stopped after the selection": (STOPPED, True),
    "orphan: asked for after the shutdown": (STOPPED, False),
    "orphan: its loader cancelled while queued": (STOPPED, False),
    # The loader dies after the load before it (which opened one).
    "orphan: its loader died": (FAILED, True),
}


@pytest.mark.parametrize("path", list(SAVE_BLOCK_PATHS))
def test_every_way_a_load_ends_gives_the_save_block_back(
        shell, ownExecutor, autonaApp, monkeypatch, path):
    """One ReleaseSaving for every BlockSaving, whichever way the attempt
    ends, and saveBlocked back at 0 (agent-pitfalls #6): a block left open
    holds every later publish back, a release too many makes SaveState run
    away."""
    from src.core.StateManager import StateManager

    games = shell.games
    outcome, opens = SAVE_BLOCK_PATHS[path]
    autonaApp.ready(1)  # held: what a load writes there waits for a commit
    shell.Load("tgame")
    drain_tasks()
    blocks = _saveBlocks(monkeypatch)
    done = []

    def finished():
        done.append(1)

    def broken(*_args, **_kwargs):
        raise RuntimeError("broken")

    if path == "skipped":
        returned = shell.Load("tgame", done=finished)
        drain_tasks()
    elif path == "SelectGame raises":
        monkeypatch.setattr(shell.catalog, "SelectGame", broken)
        returned = shell.Load("game2", done=finished)
        drain_tasks()
    elif path == "the publication raises":
        shell.Patch(monkeypatch, "publish", broken)
        returned = shell.Load("game2", done=finished)
        drain_tasks()
    elif path == "not run: the main thread is gone":
        exited = threading.Event()
        exited.set()
        monkeypatch.setattr(AutonaQtHelper, "_loopExited", exited)
        returned = shell.Load("game2", done=finished)
        _waitFor(lambda: done)
    elif path == "stopped after the selection":
        exited = threading.Event()
        monkeypatch.setattr(AutonaQtHelper, "_loopExited", exited)
        entered = _marshalEntered(monkeypatch)
        returned = shell.Load("game2", done=finished)
        assert entered.wait(WAIT)
        exited.set()
        _waitFor(lambda: done)
    elif path == "orphan: asked for after the shutdown":
        ownExecutor.shutdown(WAIT)
        returned = shell.Load("game2", done=finished)
    elif path == "orphan: its loader cancelled while queued":
        lanes = threading.Barrier(TaskExecutor.JOB_WORKERS + 1)

        def busy():
            lanes.wait(WAIT)
            ownExecutor.stopping.wait(WAIT)

        for _ in range(TaskExecutor.JOB_WORKERS):
            ownExecutor.submit(busy, task_name="busy")
        lanes.wait(WAIT)
        thread, waiting = _waitingRequest(games, "game2", done=finished)
        assert ownExecutor.shutdown(WAIT)
        thread.join(WAIT)
        assert not thread.is_alive() and "error" not in waiting
        returned = waiting["result"]
    else:  # orphan: its loader died
        entered, release = _holdTheLoader(shell.catalog, monkeypatch)

        def dies(request):
            if request.reload:
                raise RuntimeError("the loader died")

        shell.PatchSettle(monkeypatch, dies)
        shell.Load("tgame", mods_reload_mode=True)
        assert entered.wait(WAIT)
        thread, waiting = _waitingRequest(games, "game2", done=finished)
        release.set()
        _pumpUntil(lambda: not thread.is_alive(), timeout=30)
        assert "error" not in waiting
        returned = waiting["result"]
    _waitFor(lambda: not games._loaderRunning)

    assert done == [1]
    assert shell.Answer(returned, WAIT) == shell.Said(outcome)
    assert blocks["block"] == blocks["release"], blocks
    assert (blocks["block"] > 0) == opens, blocks
    assert StateManager.saveBlocked == 0


# ── each shell's own ─────────────────────────────────────────────────────


def test_a_caller_cannot_cancel_a_load_request(
        goldenGames, autonaApp, monkeypatch):
    """The desktop's: the Future a request returns is the attempt's answer,
    not a handle on it: cancel() is False whether the request is running,
    waiting for the loader, superseded or over. Cancelled, it made the
    loader's settle raise before the generation was finished, and the
    synchronous request it had superseded waited for ever (ADR-005 S3f-4
    review)."""
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


def test_the_headless_download_scan_and_loads_are_jobs(
        goldenGames, autonaApp, monkeypatch):
    """No thread of the headless loader's own any more: the start.gg
    download, the scan and the loads are jobs of the executor, which the
    shutdown stops and waits for (each was a daemon thread)."""
    from types import SimpleNamespace

    games = _headlessGames(monkeypatch, provider=SimpleNamespace(
        SetGameFromProvider=lambda: None))  # no tournament
    monkeypatch.setattr(games.catalog, "DownloadStartGGCharacters",
                        lambda: None)  # the network
    jobs = _jobsSubmitted(monkeypatch)
    threads = []
    realScan = games.catalog.ScanGames

    def scan():
        threads.append(threading.current_thread().name)
        return realScan()

    monkeypatch.setattr(games.catalog, "ScanGames", scan)
    games.Start()
    games.LoadGameByCodename("game2")
    drain_tasks()

    assert jobs[:2] == ["start.gg characters download", "game scan"]
    assert "game load" in jobs
    assert threads and threads[0].startswith(JOB_THREAD_PREFIX)
    assert not games._loaderRunning
    tree = ast.parse((PROJECT_ROOT / "src" / "headless.py")
                     .read_text(encoding="utf-8"))
    started = [ast.unparse(node.func) for node in ast.walk(tree)
               if isinstance(node, ast.Call)
               and ast.unparse(node.func).endswith("Thread")]
    assert started == []


def test_the_headless_shutdown_waits_for_a_load_and_settles_it_first(
        goldenGames, ownExecutor, autonaApp, monkeypatch):
    """S3f-4's (3): the shutdown did not wait for the loader, a daemon
    thread, so a load running then wrote the state after the flush, or was
    cut short by the interpreter's exit. It is a job now: the shutdown's
    executor stop releases the loader waiting for the main thread, the
    loader writes what follows its selection, and only then are ./out and
    state.json flushed."""
    from src import headless
    from src.core.SettingsManager import SettingsManager
    from src.core.StateManager import StateManager
    from src.core.StatePersistence import StatePersistence

    games = _headlessGames(monkeypatch)
    games.LoadGameByCodename("tgame")
    drain_tasks()
    order = []
    realPick = games._PersistPick
    monkeypatch.setattr(games, "_PersistPick",
                        lambda: (order.append("pick"), realPick()))
    realShutdown = ownExecutor.shutdown

    def shutdown(timeout):
        finished = realShutdown(timeout)
        order.append(("tasks", finished))
        return finished

    monkeypatch.setattr(ownExecutor, "shutdown", shutdown)
    monkeypatch.setattr(StateManager, "JoinExports", staticmethod(
        lambda timeout=None: order.append("out") or True))
    monkeypatch.setattr(StatePersistence, "Flush", staticmethod(
        lambda timeout=None: order.append("state.json") or True))

    games.LoadGameByCodename("game2", done=lambda: order.append("done"))
    _waitFor(lambda: StateManager.Get("game.codename") == "game2")
    time.sleep(4 * AutonaQtHelper.MARSHAL_POLL_SECS)  # in the marshal
    assert order == []
    started = time.monotonic()
    headless.HeadlessShell(app=None, games=games).Shutdown()

    assert time.monotonic() - started < 5
    assert order == ["pick", "done", ("tasks", True), "out", "state.json"]
    assert SettingsManager.Get("SELECTED_GAME_CODENAME") == "game2"
    assert not games._loaderRunning


# ── the load's save cycle and what reads it ──────────────────────────────


# Where _Publish raises outside the announcement's slots (whose exceptions
# emit reports and goes on): inside its board operation before the
# recording opens, as the recording opens, and as it closes -- after
# gameLoaded, onLoad and the save cycle's close.
PUBLISH_FAILURES = {
    "in the board operation": 0,
    "opening the recording": 0,
    "closing the recording": 1,
}


@pytest.mark.parametrize("where", list(PUBLISH_FAILURES))
def test_a_load_whose_publication_raises_fails_and_leaves_nothing_open(
        goldenGames, autonaApp, onLoadCount, monkeypatch, where):
    """_Publish raises on the GUI thread, not in a slot: the attempt is
    FAILED (done() once), its save cycle closed once (no BlockSaving left:
    agent-pitfalls #6), no recording of held writes left open or kept
    (StateManager.RecordHeldWrites), the board host's lock given back, and
    the next load goes through. What the two tests of a failing Qt model
    rebuild checked on the way, until the Qt model clean-up's PR4 removed
    the rebuild (and its FAILED settle). Raised before the announcement, the
    selection still goes out without the roster, as it did at HEAD for any
    exception but the rebuild's: the ledger's PR4 notes it."""
    from src import AutonaWebOperations
    from src.AutonaWebOperations import BoardHost
    from src.core.StateManager import StateManager

    manager = goldenGames
    autonaApp.ready(1)  # held: what the load writes waits for its commit
    assert not StateManager.HasPendingChanges("score.1")
    armed = [where]

    def fromPublish(name):
        # _Publish's board operation (AutonaGameAssetManager._Publish's
        # nested `publish`), the load's own; not another caller's.
        return name.endswith("_Publish.<locals>.publish")

    def take():
        if not armed:
            return False
        armed.clear()
        return True

    if where == "in the board operation":
        realRun = BoardHost.Run

        def run(self, op, *args, **kwargs):
            if fromPublish(op.__qualname__) and take():
                def failing():
                    raise RuntimeError("the board operation failed")
                return realRun(self, failing, *args, **kwargs)
            return realRun(self, op, *args, **kwargs)

        host = AutonaWebOperations.boardHost
        if "Run" in vars(host):  # an earlier test's patch left one there
            hostRun = vars(host)["Run"]
            monkeypatch.setattr(
                host, "Run",
                lambda op, *a, **k: run(host, op, *a, **k)
                if fromPublish(op.__qualname__) else hostRun(op, *a, **k))
        monkeypatch.setattr(BoardHost, "Run", run)
    else:
        realRecord = StateManager.RecordHeldWrites

        @contextlib.contextmanager
        def closingFails(recording):
            with recording as written:
                yield written
            raise RuntimeError("the recording failed as it closed")

        def record():
            if fromPublish(sys._getframe(1).f_code.co_qualname) and take():
                if where == "opening the recording":
                    raise RuntimeError("the recording failed as it opened")
                return closingFails(realRecord())
            return realRecord()

        monkeypatch.setattr(StateManager, "RecordHeldWrites", record)

    done = []
    future = manager.LoadGameByCodename(
        "tgame", done=lambda: done.append(1))
    drain_tasks()

    assert not armed, "the failure was not injected"
    assert future.result(0) == FAILED and done == [1]
    assert StateManager.saveBlocked == 0
    assert StateManager.Recording() is False
    assert StateManager._heldRecords == []
    assert not StateManager.HasPendingChanges("score.1")
    assert len(onLoadCount) == PUBLISH_FAILURES[where]
    # The board host's lock was given back: another thread takes it.
    lock = AutonaWebOperations.boardHost._lock
    taken = []

    def probe():
        taken.append(lock.acquire(timeout=WAIT))
        if taken[-1]:
            lock.release()

    thread = threading.Thread(target=probe)
    thread.start()
    thread.join(WAIT)
    assert taken == [True]

    after = manager.LoadGameByCodename("game2")
    drain_tasks()
    assert after.result(0) == LOADED
    assert autonaApp.get("game.codename") == "game2"
    assert len(onLoadCount) == PUBLISH_FAILURES[where] + 1
    assert StateManager.saveBlocked == 0
    assert StateManager._heldRecords == []


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
        shell, autonaApp, socketSends):
    """The game change's full state goes out after the load is published
    and after its delta: the delta first, then a full state whose revision
    is the last delta's and whose content is that revision's -- not R's
    revision over R+1's content, ahead of the delta (ADR-005 S3f-4 review).
    Then the characters. The desktop's window runs its GameChanged on
    onLoad, inside the load's save cycle; the headless shell pushes on
    gameChanged (bootstrap.wire_web_server), after the selection's cycle
    and the roster's."""
    from src.AutonaWebServer import WebServer
    from src.StateManagerBridge import StateManagerBridge

    bridge = StateManagerBridge.instance
    # What the shells wire: the bridge's deltas into the flusher (the
    # server's construction), the game change on the announcement (the
    # desktop's Window.SetGame -> GameChanged -> ws_game_changed; the
    # headless shell's wire_web_server).
    bridge.state_updated.connect(WebServer.on_program_state_update)
    shell.announced.connect(WebServer.ws_game_changed)
    try:
        shell.Load("game2")
        drain_tasks()
        # The flusher's step: the open batch, then what waited for it.
        WebServer._flush_pending_deltas()
    finally:
        shell.announced.disconnect(WebServer.ws_game_changed)
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


# ── set_game, and a synchronous load's answer ───────────────────────────


@pytest.mark.parametrize("entry", ["set_game", "a synchronous load"])
def test_a_synchronous_load_answers_once_what_follows_it_ran(
        shell, autonaApp, monkeypatch, entry):
    """On a request thread (the legacy route's: Flask's): it answers ("OK"
    for set_game) once the game is in the state and what follows its load
    -- gameLoaded, the announcement -- has run on the main thread. set_game
    no longer hops to the GUI thread: the request thread waits for the
    load."""
    from src import AutonaWebOperations as WebOps
    from src.core.StateManager import StateManager

    games = shell.games
    # The shell's registration (bootstrap_gui() / src/headless.Assemble).
    monkeypatch.setattr(WebOps, "loadGameByCodename",
                        games.LoadGameByCodename)
    events = []
    undo = [_record(games.signals.gameLoaded, events, "gameLoaded"),
            _record(shell.announced, events, "announced")]
    done = []

    def request():
        if entry == "set_game":
            answer = autonaApp.web().set_game({"codename": "game2"})
        else:
            answer = shell.Answer(shell.Load(
                "game2", async_mode=False, done=lambda: done.append(1)))
        return (answer, list(events), StateManager.Get("game.codename"),
                threading.current_thread().name)

    try:
        thread, outcome = _onAnotherThread(request)
        _pumpUntil(lambda: not thread.is_alive(), timeout=30)
    finally:
        for disconnect in undo:
            disconnect()

    assert "error" not in outcome, outcome
    answer, before, codename, requestThread = outcome["result"]
    if entry == "set_game":
        assert answer == "OK"
    else:
        assert answer == shell.Said(LOADED) and done == [1]
    assert before == [("gameLoaded", _main()), ("announced", _main())]
    assert codename == "game2"
    assert requestThread != _main()


def test_set_game_answers_ok_once_a_failed_load_is_over(
        shell, autonaApp, monkeypatch):
    """What it answered when it loaded on the GUI thread (358a9d2d: the
    error logged, then "OK"): "OK" once the attempt is over, whatever its
    outcome -- here a load that fails."""
    from src import AutonaWebOperations as WebOps
    from src.core.StateManager import StateManager

    monkeypatch.setattr(WebOps, "loadGameByCodename",
                        shell.games.LoadGameByCodename)

    def broken(*args, **kwargs):
        raise RuntimeError("a broken game")

    monkeypatch.setattr(shell.catalog, "SelectGame", broken)
    web = autonaApp.web()
    thread, outcome = _onAnotherThread(
        lambda: web.set_game({"codename": "game2"}))
    _pumpUntil(lambda: not thread.is_alive(), timeout=30)

    assert outcome == {"result": "OK"}
    assert StateManager.saveBlocked == 0


@pytest.mark.parametrize("cause", ["the shutdown", "the event loop's end"])
def test_a_synchronous_load_returns_once_the_main_thread_is_unavailable(
        shell, ownExecutor, autonaApp, monkeypatch, cause):
    """...instead of hanging, and without loading: what follows the load
    could not run on the main thread any more. A synchronous load and
    set_game ("OK") alike; nothing is announced, the game is as it was."""
    import copy

    from src import AutonaWebOperations as WebOps
    from src.core.StateManager import StateManager

    games = shell.games
    monkeypatch.setattr(WebOps, "loadGameByCodename",
                        games.LoadGameByCodename)
    game = copy.deepcopy(StateManager.Get("game"))
    if cause == "the shutdown":
        ownExecutor.shutdown(WAIT)
    else:
        exited = threading.Event()
        exited.set()
        monkeypatch.setattr(AutonaQtHelper, "_loopExited", exited)
    events = []
    undo = [_record(games.signals.gameLoaded, events, "gameLoaded"),
            _record(shell.announced, events, "announced")]
    done = []
    web = autonaApp.web()
    try:
        thread, outcome = _onAnotherThread(lambda: shell.Load(
            "game2", async_mode=False, done=lambda: done.append(1)))
        thread.join(WAIT)  # this thread does not pump
        assert not thread.is_alive()
        assert "error" not in outcome and done == [1]
        assert shell.Answer(outcome["result"]) == shell.Said(STOPPED)

        thread, outcome = _onAnotherThread(
            lambda: web.set_game({"codename": "game2"}))
        thread.join(WAIT)
        assert not thread.is_alive()
        assert outcome == {"result": "OK"}
        QApplication.processEvents()
    finally:
        for disconnect in undo:
            disconnect()

    assert events == []
    assert StateManager.Get("game") == game
    assert autonaApp.get("game.codename") != "game2"


# ── what waits for the GUI thread from a request, at shutdown ────────────


def _logRecords():
    from loguru import logger

    records = []
    sink = logger.add(lambda message: records.append(message.record),
                      level="DEBUG")
    return records, lambda: logger.remove(sink)


def test_a_request_that_needs_the_gui_thread_at_shutdown_is_unavailable(
        autonaApp, ownExecutor):
    """The shell operations and the board operations: once the app is
    shutting down, the request is answered as unavailable (503) and logged
    at INFO -- not a 500 with an ERROR traceback (ADR-005 S3f-4 review).
    The shell operations because the GUI thread will not run them. A board
    operation runs on the request's thread since ADR-005 S4ad (the desktop's
    board host ran it on the GUI thread until then, and answered so for
    that reason): the host refuses it on the same flag, before it writes
    (the next test). (An unbuilt board's build was one too: the desktop
    built a board core did not have on its GUI thread until ADR-005 S4ac-3,
    which removed the Qt SCORE; nothing is built for a read since, in
    either shell.)"""
    from src import AutonaWebOperations as WebOps
    from src.AutonaWebOperations import NotAvailable, SHUTTING_DOWN_MESSAGE

    autonaApp.ready(1)
    web = autonaApp.web()
    ownExecutor.shutdown(WAIT)
    calls = {
        "the set selection": lambda: web.open_sets(1),
        "the thumbnail": lambda: web.get_thumbnail(1, "png"),
        "an image render": lambda: WebOps.start_image_render(
            "thumbnail", ({}, {}, {}), lambda *_: None),
        "a board operation": lambda: web.swap_teams(1),
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


def test_a_board_operation_at_shutdown_writes_nothing_off_the_main_thread(
        autonaApp, ownExecutor, boardLock):
    """ADR-005 S4ad review: a board operation runs on the request's thread
    since S4ad, so nothing makes it wait for the GUI thread any more -- the
    board host refuses it on the shutdown's own flag (gui_unavailable_
    reason(): the background tasks stopped here), before it runs anything:
    the 503 of the desktop's host, which waited for the GUI thread until
    then. The main thread's operations (a hotkey) still run, and so does an
    operation already running when the shutdown began (checked on the
    outermost call: the inner ones of a composite operation are not
    refused)."""
    from src import AutonaWebOperations as WebOps
    from src.AutonaHotkeys import AutonaHotkeys
    from src.AutonaWebOperations import NotAvailable, SHUTTING_DOWN_MESSAGE
    from src.core.StateManager import StateManager

    autonaApp.ready(1)
    ran = []

    def composite():
        # The shutdown begins while it runs; its inner operation runs.
        ownExecutor.shutdown(WAIT)
        ran.append(boardLock.held())
        return WebOps.swap_teams(1)

    thread, outcome = _onAnotherThread(
        lambda: WebOps.boardHost.Run(composite))
    thread.join(WAIT)  # this thread does not pump
    assert not thread.is_alive()
    assert outcome == {"result": "OK"} and ran == [True]
    assert StateManager.Get("score.1.teamsSwapped") is True

    thread, outcome = _onAnotherThread(lambda: WebOps.swap_teams(1))
    thread.join(WAIT)
    assert not thread.is_alive()
    error = outcome.get("error")
    assert isinstance(error, NotAvailable), outcome
    assert error.message == SHUTTING_DOWN_MESSAGE
    assert StateManager.Get("score.1.teamsSwapped") is True

    AutonaHotkeys.signals.swap_teams.emit()
    assert StateManager.Get("score.1.teamsSwapped") is False


def test_a_load_s_settle_at_shutdown_runs_under_the_board_lock(
        shell, ownExecutor, autonaApp, boardLock, monkeypatch):
    """The loader's settle once the shutdown came after the selection (the
    desktop's _SettleUnannounced, STOPPED; the headless _SettleStopped), on
    the loader's thread: the roster the load would have run -- and on the
    desktop its commit, of what the roster wrote, inside its recording
    (StateManager.RecordHeldWrites; the boards' ApplyChanges until the fix
    of 2026-11-12) -- written there as one board operation, under the board
    host's lock, which the host does not refuse as it refuses a late
    request's (`atShutdown`; ADR-005 S4ad review). The pick is persisted
    after it, outside the lock."""
    from src.core.Actions import AutonaActions
    from src.core.GameAssetCatalog import GameAssetCatalog

    autonaApp.ready(1)
    actions = AutonaActions.instance
    calls = []
    monkeypatch.setattr(
        actions, "ApplyGameRoster",
        lambda replaced: calls.append(("roster", boardLock.held())) or [1])
    monkeypatch.setattr(
        actions, "CommitGameLoad",
        lambda written: calls.append(
            ("commit", written.open, boardLock.held())))
    shell.Patch(monkeypatch, "pick",
                lambda: calls.append(("pick", boardLock.held())))
    ownExecutor.shutdown(WAIT)

    thread, outcome = _onAnotherThread(lambda: shell.Real("settle")(
        GameAssetCatalog.instance.Current()))
    thread.join(WAIT)  # this thread does not pump

    assert not thread.is_alive()
    assert "error" not in outcome, outcome
    assert calls == shell.differs["settleCalls"]


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
