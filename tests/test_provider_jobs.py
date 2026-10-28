"""The tournament provider's fetches as jobs of the task executor
(ADR-005 S3f-2).

Until S3f-2 every fetch of the provider adapter (AutonaTournamentDataProvider)
was a Worker on a QThreadPool of its own, answering through the Worker's
signals object. Nothing kept that object alive until the queued answer was
delivered, so the garbage collector could take it first, and the answer was
silently lost. Pinned here:

- every fetch answers once, on the GUI thread, with nothing of the caller's
  kept and a garbage collection in between (the answer is handed to the
  long-lived GUI executor from the job's `then`); the fetch itself runs on
  a job-lane thread, called as the Worker called it (with a progress
  callback and a cancel event);
- the set list: its progress arrives ahead of its list, and only the newest
  load answers -- the one before it is told to stop and what it already
  queued is dropped;
- RunWithProvider runs its job on the job lane with its status shown and
  cleared, and starts nothing without a provider or after the shutdown;
- the provider's own entrants fetch is a job too, and the provider stops
  between requests once the app shuts down (no request, the backoff cut
  short, the entrants neither saved nor announced);
- the player DB relay no longer waits for the GUI thread
  (gui_thread_async), so a job never does;

And since S3f-3, the Start.gg provider's own parallel requests:

- they go to the fan-out lane, so fetches filling every job thread still
  finish (they were Workers on the pool those fetches ran on);
- nothing fetches the sets queued at a station any more: the fetch never
  ran, and nothing showed what it would have answered;
- the provider package has no pool or wait of its own: no
  ThreadPoolExecutor, no as_completed. (Nothing in src/ -- the adapter and
  the EVENT panel included -- names a Worker, a QRunnable or a QThreadPool:
  tests/test_task_executor.py, since ADR-005 S3f-6.)
"""
import ast
import copy
import gc
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.core.TaskExecutor import (
    FANOUT_THREAD_PREFIX, JOB_THREAD_PREFIX, TaskExecutor)

PROJECT_ROOT = Path(__file__).resolve().parents[1]

WAIT = 10  # seconds; what a test waits at most for something that must happen


def _onMain():
    return threading.current_thread() is threading.main_thread()


def _pump(done):
    """Process the GUI thread's events until `done()` (or WAIT seconds)."""
    from qtpy.QtCore import QCoreApplication
    deadline = time.monotonic() + WAIT
    while not done() and time.monotonic() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.001)
    # Anything else already queued (a second answer would be) lands too.
    QCoreApplication.processEvents()


@pytest.fixture
def adapter(autonaApp, monkeypatch):
    """The session's provider adapter, with no provider of the session's
    loaded; every job it started is drained afterwards."""
    from src.AutonaTournamentDataProvider import AutonaTournamentDataProvider
    adapter = AutonaTournamentDataProvider.instance
    monkeypatch.setattr(adapter, "provider", None)
    try:
        yield adapter
    finally:
        assert TaskExecutor.instance.drain(WAIT)
        _pump(lambda: True)


@pytest.fixture
def executor(autonaApp, monkeypatch):
    """Put an executor of the test's own in place of the session's (whose
    jobs are finished first), and shut it down afterwards."""
    assert TaskExecutor.instance.drain(WAIT)
    made = []

    def make(**kwargs):
        made.append(TaskExecutor(**kwargs))
        monkeypatch.setattr(TaskExecutor, "instance", made[-1])
        return made[-1]

    try:
        yield make
    finally:
        for each in made:
            each.shutdown(WAIT)


class _Fetch:
    """A provider method: records how it was called and on which thread,
    and answers `value` once the gate opens."""

    def __init__(self, gate, value, progress=()):
        self.gate = gate
        self.value = value
        self.progress = progress
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append((threading.current_thread().name, kwargs))
        assert self.gate.wait(WAIT)
        for step in self.progress:
            kwargs["progress_callback"](*step)
        return self.value


def _connect(signal, answers, tag):
    def record(*args):
        answers.append((tag, args, _onMain()))
    signal.connect(record)
    return lambda: signal.disconnect(record)


SETS = [{"id": 1}, {"id": 2}]

# kind -> (provider method, its answer, the answers the app sees)
FETCHES = {
    "tournament data": (
        "GetTournamentData", {"tournamentName": "T"},
        [("data", ({"tournamentName": "T", "initial_load": False},))]),
    "phases": (
        "GetTournamentPhases", [{"id": 7, "name": "Pools"}],
        [("phases", ([{"id": 7, "name": "Pools"}],))]),
    "analysis": (
        "GetAnalysisPhaseGroup", {"sets": []},
        [("analysis", ({"sets": []},))]),
    "stations": (
        "GetStations", [{"id": 3}],
        [("stations", ([{"id": 3}],))]),
    "standings": (
        "GetStandings", [{"placement": 1}],
        [("standings", ([{"placement": 1}],))]),
    "set list": (
        "GetMatches", SETS,
        [("progress", ({"progress": 1, "totalPages": 2, "sets": []},)),
         ("progress", ({"progress": 2, "totalPages": 2, "sets": []},)),
         ("finished", (SETS,)),
         ("progress", ({"progress": 0, "totalPages": 0, "sets": SETS},))]),
}


def _start(adapter, kind, answers):
    """Ask for `kind` the way the app does, keeping nothing of the call;
    the disconnects."""
    signals = adapter.signals
    if kind == "tournament data":
        undo = [_connect(signals.tournament_data_updated, answers, "data")]
        adapter.GetTournamentData()
    elif kind == "phases":
        undo = [_connect(signals.tournament_phases_updated, answers,
                         "phases")]
        adapter.GetTournamentPhases()
    elif kind == "analysis":
        undo = []
        adapter.GetAnalysisPhaseGroup(
            42, lambda data: answers.append(("analysis", (data,), _onMain())))
    elif kind == "stations":
        undo = [_connect(signals.get_stations_finished, answers, "stations")]
        adapter.LoadStations()
    elif kind == "standings":
        undo = []
        adapter.GetStandings(1, SimpleNamespace(
            emit=lambda data: answers.append(
                ("standings", (data,), _onMain()))))
    else:
        undo = [_connect(signals.sets_data_updated, answers, "progress"),
                _connect(signals.get_sets_finished, answers, "finished")]
        adapter.LoadSets(True)
    return undo


@pytest.mark.parametrize("kind", list(FETCHES))
def test_each_fetch_answers_once_on_the_gui_thread_after_a_collection(
        adapter, kind):
    method, value, expected = FETCHES[kind]
    gate = threading.Event()
    # A copy: the adapter completes the tournament data in place.
    fetch = _Fetch(gate, copy.deepcopy(value),
                   progress=[(1, 2), (2, 2)] if kind == "set list" else ())
    adapter.provider = SimpleNamespace(**{method: fetch})
    answers = []

    undo = _start(adapter, kind, answers)
    try:
        # The job is in flight and nothing of the call is referenced: what
        # used to carry the answer (a Worker's signals) could go here.
        gc.collect()
        gate.set()
        assert TaskExecutor.instance.drain(WAIT)
        gc.collect()
        assert answers == []  # handed to the GUI thread, not delivered yet
        _pump(lambda: len(answers) >= len(expected))
    finally:
        for disconnect in undo:
            disconnect()

    assert [(tag, args) for tag, args, _main in answers] == expected
    assert all(main for _tag, _args, main in answers)
    [(thread, kwargs)] = fetch.calls
    assert thread.startswith(JOB_THREAD_PREFIX)
    # Called as the Worker called it.
    assert "progress_callback" in kwargs and "cancel_event" in kwargs


def test_a_newer_set_list_load_drops_what_the_older_one_queued(
        adapter, executor):
    """The window asks again while the first list is on its way: the first
    load is told to stop (its cancel event) and its answers, already queued
    to the GUI thread, are dropped. Until S3f-2 the old Worker's signals
    were blocked, which let an answer already queued through."""
    jobs = executor(stepped=True)
    gate = threading.Event()
    gate.set()
    first = _Fetch(gate, [{"id": "old"}], progress=[(1, 1)])
    second = _Fetch(gate, [{"id": "new"}], progress=[(1, 1)])
    answers = []
    undo = [_connect(adapter.signals.sets_data_updated, answers, "progress"),
            _connect(adapter.signals.get_sets_finished, answers, "finished")]
    try:
        adapter.provider = SimpleNamespace(GetMatches=first)
        adapter.LoadSets(False)
        assert jobs.run_next()  # its progress and list are queued now

        adapter.provider = SimpleNamespace(GetMatches=second)
        adapter.LoadSets(False)
        assert jobs.run_next()
        _pump(lambda: len(answers) >= 3)
    finally:
        for disconnect in undo:
            disconnect()

    new = [{"id": "new"}]
    assert [(tag, args) for tag, args, _main in answers] == [
        ("progress", ({"progress": 1, "totalPages": 1, "sets": []},)),
        ("finished", (new,)),
        ("progress", ({"progress": 0, "totalPages": 0, "sets": new},)),
    ]
    assert first.calls[0][1]["cancel_event"].is_set()
    assert not second.calls[0][1]["cancel_event"].is_set()


def test_the_statistics_fetches_are_jobs_called_as_the_worker_called_them(
        adapter):
    gate = threading.Event()
    gate.set()
    fetches = {name: _Fetch(gate, None) for name in (
        "GetRecentSets", "GetLastSets", "GetPlayerHistoryStandings")}
    adapter.provider = SimpleNamespace(**fetches)
    answered = []
    callback = SimpleNamespace(emit=answered.append)

    adapter.GetRecentSets(callback, [1, 10], [2, 20], 1386)
    adapter.GetLastSets(callback, [1, 10], 1)
    adapter.GetPlayerHistoryStandings(callback, [1, 10], 2, 1386)
    assert TaskExecutor.instance.drain(WAIT)

    for name, fetch in fetches.items():
        [(thread, kwargs)] = fetch.calls
        assert thread.startswith(JOB_THREAD_PREFIX), name
        assert "progress_callback" in kwargs and "cancel_event" in kwargs
        # The provider answers on what it is handed: the caller's callback
        # (behind the shutdown's check, see the test below).
        kwargs["callback"].emit(name)
    assert answered == list(fetches)
    assert fetches["GetLastSets"].calls[0][1]["playerID"] == 1


def test_cancelling_the_set_list_drops_what_it_queued(adapter, executor):
    """A set picked while the list loads (the set selection window): the
    load is told to stop and its answers, queued or not, are dropped."""
    jobs = executor(stepped=True)
    gate = threading.Event()
    gate.set()
    fetch = _Fetch(gate, [{"id": 1}], progress=[(1, 1)])
    answers = []
    undo = [_connect(adapter.signals.sets_data_updated, answers, "progress"),
            _connect(adapter.signals.get_sets_finished, answers, "finished")]
    try:
        adapter.provider = SimpleNamespace(GetMatches=fetch)
        adapter.LoadSets(False)
        assert jobs.run_next()  # its progress and list are queued now
        adapter.CancelSetLoad()
        _flush()
    finally:
        for disconnect in undo:
            disconnect()

    assert answers == []
    assert fetch.calls[0][1]["cancel_event"].is_set()


def _flush():
    """Process what the GUI thread has queued (nothing is expected)."""
    from qtpy.QtCore import QCoreApplication
    for _ in range(3):
        QCoreApplication.processEvents()


@pytest.mark.parametrize("path", ["the adapter's fetch", "a statistic",
                                  "the set follow's fetch"])
def test_an_answer_made_after_the_stop_is_dropped(adapter, executor, path):
    """The app quits while a fetch runs: the shutdown cut its requests
    short, so it answers {} or an empty list. That is no answer: written,
    it would empty the tournament info, a statistic or the stream queue
    while the shutdown waits (ADR-005 S3f-2 review)."""
    from src.core.SetFollowService import SetFollowService

    jobs = executor()
    entered, gate = threading.Event(), threading.Event()
    answers = []

    def blocking(**kwargs):
        entered.set()
        assert gate.wait(WAIT)
        if "callback" in kwargs:
            kwargs["callback"].emit([])
        return {}

    undo = []
    try:
        if path == "the adapter's fetch":
            adapter.provider = SimpleNamespace(GetTournamentData=blocking)
            undo.append(_connect(adapter.signals.tournament_data_updated,
                                 answers, "data"))
            adapter.GetTournamentData()
        elif path == "a statistic":
            adapter.provider = SimpleNamespace(GetLastSets=blocking)
            adapter.GetLastSets(SimpleNamespace(emit=answers.append),
                                [1, 10], 1)
        else:
            adapter.provider = SimpleNamespace(GetStreamQueue=blocking)
            undo.append(_connect(adapter.signals.stream_queue_loaded,
                                 answers, "queue"))
            assert SetFollowService.fetcher.StreamQueue()
        assert entered.wait(WAIT)
        assert jobs.shutdown(0.05) is False  # stopping; the fetch still runs
        gate.set()
        assert jobs.shutdown(WAIT)           # and now it has answered
        _flush()
    finally:
        for disconnect in undo:
            disconnect()

    assert answers == []


def test_run_with_provider_runs_on_the_job_lane_with_its_status(
        adapter, monkeypatch):
    from src.Helpers.AutonaStatusManager import AutonaStatusManager
    shown = []
    push, pop = AutonaStatusManager.push, AutonaStatusManager.pop

    def spyPush(message, *args):
        token = push(message, *args)
        shown.append(("push", message, token))
        return token

    def spyPop(token):
        shown.append(("pop", token))
        pop(token)

    monkeypatch.setattr(AutonaStatusManager, "push", spyPush)
    monkeypatch.setattr(AutonaStatusManager, "pop", spyPop)
    provider = adapter.provider = SimpleNamespace(name="fake")
    ran = []

    assert adapter.RunWithProvider(
        lambda given: ran.append((given, threading.current_thread().name)),
        status="working")
    assert TaskExecutor.instance.drain(WAIT)

    [(given, thread)] = ran
    assert given is provider and thread.startswith(JOB_THREAD_PREFIX)
    token = shown[0][-1]
    assert shown == [("push", "working", token), ("pop", token)]


def test_run_with_provider_starts_nothing_without_a_provider_or_after_the_shutdown(
        adapter, executor):
    ran = []
    assert adapter.RunWithProvider(ran.append) is False

    adapter.provider = SimpleNamespace(name="fake")
    executor().shutdown(WAIT)
    assert adapter.RunWithProvider(ran.append) is False
    assert ran == []


# ── the Start.gg provider ────────────────────────────────────────────────


URL = "https://www.start.gg/tournament/t/event/e"


@pytest.fixture
def startgg(autonaApp):
    from src.TournamentDataProvider.StartGGDataProvider import (
        StartGGDataProvider)
    return StartGGDataProvider(URL, None)


def test_the_entrants_fetch_is_a_job(startgg, executor, monkeypatch):
    jobs = executor(stepped=True)
    calls = []
    monkeypatch.setattr(startgg, "GetEntrantsWorker", lambda **kwargs: calls.append(
        (threading.current_thread().name, kwargs)))

    startgg.GetEntrants()
    assert jobs.queued() == 1 and calls == []
    assert jobs.run_next()

    [(thread, kwargs)] = calls
    assert thread.startswith(JOB_THREAD_PREFIX)
    assert kwargs == {"eventSlug": "tournament/t/event/e",
                      "progress_callback": None, "cancel_event": None}


def test_no_request_is_made_once_the_app_shuts_down(startgg, executor):
    executor().shutdown(WAIT)
    requests = []

    assert startgg.QueryRequests(
        "https://example.invalid", type=lambda *a, **k: requests.append(a)) \
        == {}
    assert requests == []


def test_the_shutdown_cuts_the_retry_backoff_short(startgg, executor):
    jobs = executor()
    requests = []

    def failing(*args, **kwargs):
        requests.append(args)
        jobs.shutdown(WAIT)  # the app quits while this request fails
        raise ConnectionError("down")

    started = time.monotonic()
    assert startgg.QueryRequests("https://example.invalid", type=failing) == {}

    assert len(requests) == 1
    # The first backoff alone is a second when nothing stops it.
    assert time.monotonic() - started < 0.9


@pytest.mark.parametrize("when", ["between pages", "during a request",
                                  "another tournament was loaded",
                                  "never (the control)"])
def test_the_entrants_fetch_stops_without_saving_or_announcing(
        startgg, executor, monkeypatch, when):
    from src.AutonaPlayerDB import AutonaPlayerDB
    from src.core.PlayerDB import PlayerDB
    from src.Helpers.AutonaStatusManager import AutonaStatusManager

    jobs = executor()
    done = []
    monkeypatch.setattr(PlayerDB.instance, "AddPlayers",
                        lambda players, deferRefresh=False: None)
    monkeypatch.setattr(PlayerDB.instance, "SaveDB",
                        lambda: done.append("save"))
    monkeypatch.setattr(PlayerDB.instance, "NotifyChanged",
                        lambda: done.append("announce"))
    monkeypatch.setattr(
        AutonaPlayerDB, "SetTournamentPlayers",
        lambda tags, cancel_event=None: done.append(("tags", cancel_event)))
    startgg.tshTdp = SimpleNamespace(signals=SimpleNamespace(
        entrants_updated=SimpleNamespace(emit=lambda: done.append("emit"))))
    popped = []
    pop = AutonaStatusManager.pop
    monkeypatch.setattr(AutonaStatusManager, "pop",
                        lambda token: (popped.append(token), pop(token)))
    pages = []
    cancel = threading.Event()

    def query(*args, **kwargs):
        pages.append(kwargs["jsonParams"]["variables"]["page"])
        if when == "another tournament was loaded":
            cancel.set()  # the adapter's, on the GUI thread
        elif when != "never (the control)":
            jobs.shutdown(WAIT)
        if when == "during a request":
            return {}  # what QueryRequests answers once it is stopped
        return {"data": {"event": {"entrants": {
            "pageInfo": {"totalPages": 2}, "nodes": []}}}}

    monkeypatch.setattr(startgg, "QueryRequests", query)
    startgg.GetEntrantsWorker("tournament/t/event/e", None, cancel)

    assert len(popped) == 1  # the status is cleared either way
    if when == "never (the control)":
        assert pages == [1, 2]
        # The tags go with the fetch's cancel event: the GUI thread drops
        # them if another tournament was loaded by then (test below).
        assert done == ["save", ("tags", cancel), "announce", "emit"]
    else:
        assert pages == [1]
        assert done == []


def test_the_parallel_requests_finish_with_the_job_lane_full(
        startgg, executor, monkeypatch):
    """Every job thread runs a fetch that makes requests in parallel (a
    set's two APIs, a head-to-head's ten pages), and they all finish: the
    requests go to the fan-out lane (ADR-005 S3f-3). Until then they were
    Workers queued on the pool the fetches themselves ran on, which a full
    pool never ran -- and the head-to-head waited for them with no bound."""
    jobs = executor()
    full = threading.Barrier(TaskExecutor.JOB_WORKERS, timeout=WAIT)
    sentFrom = []

    def query(url=None, type=None, headers={}, jsonParams=None, params=None):
        sentFrom.append(threading.current_thread().name)
        return {}

    monkeypatch.setattr(startgg, "QueryRequests", query)
    answers = []

    def fetch(kind):
        full.wait()  # every job thread is one of these fetches now
        if kind == "set":
            return startgg.GetMatch(setId=1)
        startgg.GetRecentSets([1, 10], [2, 20], 1386,
                              SimpleNamespace(emit=answers.append), 7,
                              None, None)

    kinds = ["set", "recent sets"] * (TaskExecutor.JOB_WORKERS // 2)
    futures = [jobs.submit(fetch, kind, task_name=f"test {kind}")
               for kind in kinds]
    assert jobs.drain(WAIT)

    assert [future.exception() for future in futures] == [None] * len(kinds)
    assert all(isinstance(future.result(), dict)
               for future, kind in zip(futures, kinds) if kind == "set")
    assert answers == [{"sets": [], "request_time": 7}] * kinds.count(
        "recent sets")
    assert len(sentFrom) == 2 * kinds.count("set") \
        + 10 * kinds.count("recent sets")
    assert all(name.startswith(FANOUT_THREAD_PREFIX) for name in sentFrom)


def test_loading_another_tournament_cancels_the_entrants_fetch(
        adapter, executor, monkeypatch):
    """Each tournament's entrants fetch has a cancel event of its own, set
    once another tournament, the same one forced again, or none is loaded
    (ADR-005 S3f-2 review: a replaced tournament's fetch kept paging and
    finally installed its players over the new tournament's filter, or
    over the cleared one). Nothing is fetched: the jobs stay queued on a
    stepped executor."""
    from src.core.SettingsManager import SettingsManager
    from src.TournamentDataProvider.StartGGDataProvider import (
        StartGGDataProvider)

    executor(stepped=True)
    asked = []
    monkeypatch.setattr(StartGGDataProvider, "GetEntrants",
                        lambda self, cancel_event=None: asked.append(
                            cancel_event))
    previous = SettingsManager.Get("TOURNAMENT_URL")
    try:
        adapter.SetTournament(URL)
        adapter.SetTournament(URL)  # the same one: nothing happens
        adapter.SetTournament(URL, force=True)
        adapter.SetTournament("https://www.start.gg/tournament/t/event/f")
        adapter.SetTournament(None)
    finally:
        SettingsManager.Set("TOURNAMENT_URL", previous)

    assert len(asked) == 3
    assert all(event.is_set() for event in asked)


def test_a_replaced_tournaments_players_are_dropped_on_the_gui_thread(
        autonaApp, monkeypatch):
    """The fetch handed its tags over before its cancel event was set: the
    GUI thread, where the event is set, drops them."""
    from src.AutonaPlayerDB import AutonaPlayerDB
    from src.core.PlayerDB import PlayerDB

    installed = []
    monkeypatch.setattr(PlayerDB.instance, "SetTournamentTags",
                        installed.append)
    cancel = threading.Event()
    cancel.set()
    AutonaPlayerDB.SetTournamentPlayers({"A"}, cancel_event=cancel)
    assert installed == []

    AutonaPlayerDB.SetTournamentPlayers({"B"}, cancel_event=threading.Event())
    assert installed == [{"B"}]


# ── the player DB relay ──────────────────────────────────────────────────


def _decorators(path, cls):
    tree = ast.parse((PROJECT_ROOT / path).read_text(encoding="utf-8"))
    klass = next(node for node in ast.walk(tree)
                 if isinstance(node, ast.ClassDef) and node.name == cls)
    return {node.name: [ast.unparse(d) for d in node.decorator_list]
            for node in klass.body if isinstance(node, ast.FunctionDef)}


def test_the_player_db_relay_never_waits_for_the_gui_thread():
    """A job (the entrants fetch, a web save) calls these: none of them may
    wait for the GUI thread, which never answers once the event loop has
    stopped -- and the interpreter's exit joins the job (S3f-2 decision 4).
    The waiting behaviour is pinned in test_player_db.py."""
    decorators = _decorators("src/AutonaPlayerDB.py", "AutonaPlayerDB")
    for name in ("SetupModel", "SetTournamentPlayers",
                 "ClearTournamentPlayers"):
        assert decorators[name] == ["staticmethod", "gui_thread_async"], name
    tree = ast.parse((PROJECT_ROOT / "src" / "AutonaPlayerDB.py").read_text(
        encoding="utf-8"))
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    names |= {alias.name for node in ast.walk(tree)
              if isinstance(node, ast.ImportFrom) for alias in node.names}
    assert "gui_thread_sync" not in names


# ── no pool of the provider's own ────────────────────────────────────────


def _names(path):
    """Every name, attribute and imported name or module part in `path`."""
    tree = ast.parse((PROJECT_ROOT / path).read_text(encoding="utf-8"))
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


# The provider package has no pool of its own: the requests a fetch makes
# in parallel go to the task executor's fan-out lane (ADR-005 S3f-3). What
# no file of src/ may name (a Worker, a QThreadPool...) is pinned by
# tests/test_task_executor.py; these are the provider's own (the executor
# itself is a ThreadPoolExecutor, so they are not banned everywhere).
PROVIDER_FORBIDDEN = {"ThreadPoolExecutor", "as_completed",
                      "getStationMatchesThreadPool"}


@pytest.mark.parametrize("path", sorted(
    path.relative_to(PROJECT_ROOT).as_posix()
    for path in (PROJECT_ROOT / "src" / "TournamentDataProvider").glob("*.py")))
def test_the_provider_package_has_no_worker_or_pool(path):
    assert not _names(path) & PROVIDER_FORBIDDEN


# The sets queued behind a station's current one are not fetched (ADR-005
# S3f-3): the fetch never ran, and nothing showed what it would have
# answered (`score.{n}.station_queue` stays {}), so making it work only
# added start.gg requests. It went with its callers and its query.
FUTURE_SETS = {"FutureSets", "GetFutureMatchesList", "GetFutureMatch",
               "FutureSetQuery"}


def test_nothing_fetches_the_sets_queued_at_a_station():
    """No file of src/ calls, defines or sends (as an operation name) the
    future-set fetch, and its query file is gone."""
    named = set()
    for path in sorted((PROJECT_ROOT / "src").rglob("*.py")):
        relative = path.relative_to(PROJECT_ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        defined = {node.name for node in ast.walk(tree)
                   if isinstance(node, (ast.FunctionDef,
                                        ast.AsyncFunctionDef, ast.ClassDef))}
        strings = {node.value for node in ast.walk(tree)
                   if isinstance(node, ast.Constant)
                   and isinstance(node.value, str)}
        if (_names(relative) | defined | strings) & FUTURE_SETS:
            named.add(relative)
    assert named == set()
    assert not (PROJECT_ROOT / "src" / "TournamentDataProvider"
                / "StartGGFutureSetQuery.txt").exists()


def test_what_the_app_reads_on_the_adapter_exists(autonaApp):
    """Every `AutonaTournamentDataProvider.instance.<name>` in src/ names
    something the adapter has. The set selection window still read the
    removed `setLoadingWorker` after S3f-2 -- an AttributeError on every set
    picked -- and its test's stub, which had one, hid it (review)."""
    from src.AutonaTournamentDataProvider import AutonaTournamentDataProvider
    adapter = AutonaTournamentDataProvider.instance
    missing = set()
    for path in sorted((PROJECT_ROOT / "src").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Attribute)
                    and ast.unparse(node.value)
                    == "AutonaTournamentDataProvider.instance"
                    and not hasattr(adapter, node.attr)):
                missing.add(f"{path.relative_to(PROJECT_ROOT)}: {node.attr}")
    assert not missing
