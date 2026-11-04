"""AnalysisService (この大会の試合一覧, ADR-005 S4j): the fetch.

One fetch at a time (BUSY), two phase groups at a time (one while a
dropped fetch's job is still out), dropped with the tournament -- what its
jobs still get back included, and a fetch asked for across the switch --,
a failed request told from an empty answer (the real provider's, asked
strictly), run by the headless shell too, and nothing waited on inside
StateManager.lock. What the rows are is tests/test_analysis_rows.py's (the
frozen golden of the pre-S4j panel).
"""
import copy
import json
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from src.core import StateSchema as schema
from src.core.AnalysisService import (
    BUSY, NO_TOURNAMENT, STARTED, AnalysisService, PhaseGroupEntries,
    RoundPrefix)
from src.core.StateManager import StateManager
from tests.test_api_v1 import webApp  # noqa: F401
from tests.test_analysis_rows import (
    BYES, EXPECTED_PROGRESS, SCENARIOS, FakeProvider, _drain, _expectedRows,
    _pageCells, _phases, _provider, _switchTournament,
    analysis)  # noqa: F401  (the fixture)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WAIT = 10


def _rows(service):
    return sorted((_pageCells(row) for row in service.Rows()),
                  key=lambda row: row[0][1])


def _until(app, condition, what):
    """Run the event loop until `condition()`, for at most WAIT seconds."""
    from qtpy.QtCore import QCoreApplication
    deadline = time.monotonic() + WAIT
    while not condition():
        assert time.monotonic() < deadline, f"never: {what}"
        QCoreApplication.processEvents()
        QCoreApplication.sendPostedEvents()
        time.sleep(0.005)


def _spin(app, rounds=40):
    """The event loop and the jobs, given a while to do what they would."""
    from qtpy.QtCore import QCoreApplication
    for _ in range(rounds):
        QCoreApplication.processEvents()
        QCoreApplication.sendPostedEvents()
        time.sleep(0.005)


class GatedProvider(FakeProvider):
    """Holds its answers until `release`: the phases (gatePhases) or every
    group's. Counts the groups in flight, and on which threads."""

    def __init__(self, phases, groups, gatePhases=False):
        super().__init__(phases, groups)
        self.gatePhases = gatePhases
        self.release = threading.Event()
        self.phasesAsked = threading.Event()
        self._count = threading.Lock()
        self.inFlight = 0
        self.most = 0
        self.threads = set()
        self.asked = []             # the groups asked, answered or not

    def GetTournamentPhases(self, progress_callback=None, cancel_event=None,
                            strict=False):
        if self.gatePhases:
            self.phasesAsked.set()
            assert self.release.wait(WAIT), "the test never released it"
        return super().GetTournamentPhases(strict=strict)

    def GetAnalysisPhaseGroup(self, id, progress_callback=None,
                              cancel_event=None, strict=False):
        with self._count:
            self.inFlight += 1
            self.most = max(self.most, self.inFlight)
            self.asked.append(id)
            self.threads.add(threading.current_thread().name)
        try:
            if not self.gatePhases:
                assert self.release.wait(WAIT), "the test never released it"
            return super().GetAnalysisPhaseGroup(id, strict=strict)
        finally:
            with self._count:
                self.inFlight -= 1


class _OwnedLock:
    """The service's plain Lock, telling whether this thread holds it. Not
    reentrant, as the service's: taken again by its holder, it fails the
    test (where the service would deadlock)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._owner = None

    def __enter__(self):
        assert self._owner != threading.get_ident(), \
            "the service's lock taken again by the thread that holds it"
        assert self._lock.acquire(timeout=WAIT), \
            "the service's lock was never released"
        self._owner = threading.get_ident()
        return self

    def __exit__(self, *exc):
        self._owner = None
        self._lock.release()

    def _is_owned(self):
        return self._owner == threading.get_ident()


@pytest.fixture
def service(analysis, monkeypatch):  # noqa: F811
    """An AnalysisService of the test's own, wired as bootstrap_core wires
    the session's, in its place; it records where its fetcher and its
    `later` are called from (thread, StateManager.lock held, its own lock
    held)."""
    from src.AutonaTournamentDataProvider import AutonaTournamentDataProvider
    from src.Helpers import AutonaQtHelper
    from src.Helpers.AutonaLocaleHelper import AutonaLocaleHelper
    calls = []

    def where(kind):
        own = made._lock
        calls.append((kind, threading.current_thread().name,
                      StateManager.lock._is_owned(), own._is_owned()))

    def fetcher(job, status):
        where("fetcher")
        return AutonaTournamentDataProvider.instance.RunWithProvider(
            job, status)

    def later(fn):
        where("later")
        AutonaQtHelper.gui_executor.later(fn)

    made = AnalysisService(
        fetcher=fetcher, later=later,
        roundNames=lambda: AutonaLocaleHelper.matchNames)
    made._lock = _OwnedLock()
    made.calls = calls
    monkeypatch.setattr(AnalysisService, "instance", made)
    try:
        yield made
    finally:
        # Its jobs still out (a test that failed) record nothing more.
        made.Forget()


# ── one at a time ────────────────────────────────────────────────────────

def test_a_refresh_while_one_runs_is_busy(service, analysis):  # noqa: F811
    provider = GatedProvider(*SCENARIOS["one group, seeds known"],
                             gatePhases=True)
    _switchTournament(provider)
    revision = AnalysisService.Progress()["revision"]
    try:
        assert service.Refresh() == STARTED
        assert provider.phasesAsked.wait(WAIT)
        assert service.Running()
        # Published before Refresh returned: the phases are being fetched.
        running = dict(schema.DefaultAnalysis(revision + 1),
                       status="running")
        assert AnalysisService.Progress() == running

        assert service.Refresh() == BUSY
        assert AnalysisService.Progress() == running
    finally:
        provider.release.set()
        _drain(analysis)

    assert not service.Running()
    assert AnalysisService.Progress() == dict(
        EXPECTED_PROGRESS["one group, seeds known"], revision=revision + 1)
    assert provider.calls == [["phases"], ["group", 50]]      # one fetch
    # Over: the next one starts, at the next revision.
    assert service.Refresh() == STARTED
    _drain(analysis)
    assert provider.calls[2:] == [["phases"], ["group", 50]]
    assert AnalysisService.Progress()["revision"] == revision + 2


def test_without_a_tournament_or_a_fetcher_nothing_starts(
        service, analysis, monkeypatch):  # noqa: F811
    _switchTournament(None)
    before = StateManager.Get("analysis")
    assert service.Refresh() == NO_TOURNAMENT
    assert StateManager.Get("analysis") == before
    assert not service.Running()

    # No shell registered a fetcher: the same answer.
    bare = AnalysisService()
    monkeypatch.setattr(AnalysisService, "instance", bare)
    _switchTournament(_provider("one group, byes"))
    before = StateManager.Get("analysis")
    assert bare.Refresh() == NO_TOURNAMENT
    assert StateManager.Get("analysis") == before
    assert not bare.Running()


# ── dropped with the tournament ──────────────────────────────────────────

def test_a_tournament_switch_drops_the_fetch_and_what_it_still_gets(
        service, analysis):  # noqa: F811
    """The tournament is loaded again while a group's answer is on its way
    (the provider adapter's LoadTournament calls Forget): the fetch is
    dropped at once -- a new one may start, not BUSY -- and the answer it
    gets back afterwards is recorded nowhere, the new fetch's count
    included."""
    label = "one group, seeds known"
    provider = GatedProvider(*SCENARIOS[label])
    _switchTournament(provider)
    try:
        assert service.Refresh() == STARTED
        _until(analysis, lambda: provider.inFlight == 1, "the group asked")
        running = AnalysisService.Progress()
        assert (running["status"], running["done"], running["total"]) \
            == ("running", 0, 1)

        _switchTournament(provider)
        forgotten = AnalysisService.Progress()
        assert forgotten == schema.DefaultAnalysis(running["revision"] + 1)
        assert not service.Running()
        assert service.Rows() == []

        assert service.Refresh() == STARTED
        _until(analysis, lambda: provider.inFlight == 2,
               "the new fetch's group asked, the dropped one's still out")
    finally:
        provider.release.set()
        _drain(analysis)

    assert AnalysisService.Progress() == dict(
        EXPECTED_PROGRESS[label], revision=forgotten["revision"] + 1)
    assert _rows(service) == _expectedRows(label)
    assert sorted(provider.calls, key=json.dumps) == [
        ["group", 50], ["group", 50], ["phases"], ["phases"]]


def test_a_dropped_fetch_publishes_nothing_more(service, analysis):  # noqa: F811
    """Unloaded while its phases are on their way: idle stays published
    after they come back, and no row is made."""
    provider = GatedProvider(*SCENARIOS["one group, byes"], gatePhases=True)
    _switchTournament(provider)
    try:
        assert service.Refresh() == STARTED
        assert provider.phasesAsked.wait(WAIT)
        _switchTournament(None)
        forgotten = AnalysisService.Progress()
        assert forgotten["status"] == "idle"
    finally:
        provider.release.set()
        _drain(analysis)
    assert AnalysisService.Progress() == forgotten
    assert service.Result() == {"url": "", "rows": [], "revision": None}
    assert provider.calls == [["phases"]]         # its groups never asked


def test_forgetting_drops_the_rows_and_advances_the_revision(
        service, analysis):  # noqa: F811
    label = "one group, byes"
    _switchTournament(_provider(label))
    assert service.Refresh() == STARTED
    _drain(analysis)
    done = AnalysisService.Progress()
    result = service.Result()
    assert result["revision"] == done["revision"]
    assert result["url"] == FakeProvider.url
    assert _rows(service) == _expectedRows(label)

    _switchTournament(None)
    assert service.Result() == {"url": "", "rows": [], "revision": None}
    assert AnalysisService.Progress() == schema.DefaultAnalysis(
        done["revision"] + 1)
    # Nothing left to drop: it still tells the readers the tournament
    # changed.
    service.Forget()
    assert AnalysisService.Progress()["revision"] == done["revision"] + 2


def test_the_provider_adapter_forgets_on_every_load_and_unload(
        service, adapter, executor, monkeypatch):
    """Called directly by LoadTournament / UnloadTournament, once the new
    provider (or none) is in place (no state observer; S4j review M2).
    Nothing is fetched: the adapter's jobs stay queued on a stepped
    executor."""
    from src.TournamentDataProvider.StartGGDataProvider import (
        StartGGDataProvider)
    from tests.test_provider_jobs import URL
    executor(stepped=True)
    monkeypatch.setattr(StartGGDataProvider, "GetEntrants",
                        lambda self, cancel_event=None: None)
    forgotten = []
    monkeypatch.setattr(service, "Forget", lambda: forgotten.append(
        getattr(adapter.provider, "url", None)))

    assert adapter.LoadTournament(URL) is True
    first = adapter.provider
    assert adapter.LoadTournament(URL) is False    # the same one: nothing
    assert adapter.LoadTournament(URL, force=True) is True
    assert adapter.provider is not first
    adapter.UnloadTournament()
    # Each time with the tournament it loads already in place.
    assert forgotten == [URL, URL, None]


NEW_URL = "https://www.start.gg/tournament/next/event/analysis"


def _onAnotherThread(fn):
    """`fn()` on a thread of its own (the Web's, S4k), waited for."""
    box = {}
    thread = threading.Thread(target=lambda: box.update(value=fn()))
    thread.start()
    thread.join(WAIT)
    assert not thread.is_alive()
    return box["value"]


def test_a_refresh_across_a_tournament_switch_never_shows_the_old_rows(
        service, analysis, monkeypatch):  # noqa: F811
    """Another thread asks for a fetch right as a tournament is loaded:
    just before the adapter drops the match list, and just after. Neither
    makes the replaced tournament's rows the new one's -- the one before
    is dropped, the one after fetches from the new provider -- because the
    drop comes once the new provider is in place (S4j review M2. With the
    drop first, the one after fetched from the replaced provider and its
    rows ended as the new tournament's)."""
    import src.AutonaTournamentDataProvider as module
    adapter = module.AutonaTournamentDataProvider.instance
    old = _provider("one group, seeds known")
    new = _provider("one group, byes")
    new.url = NEW_URL
    monkeypatch.setattr(module, "StartGGDataProvider",
                        lambda url, parent: {NEW_URL: new}[url])
    for name in ("GetTournamentData", "GetTournamentPhases",
                 "_StartEntrants"):
        monkeypatch.setattr(adapter, name, lambda *args, **kwargs: None)
    _switchTournament(old)
    answers = []
    forget = service.Forget

    def crossed():
        answers.append(_onAnotherThread(service.Refresh))
        forget()
        answers.append(_onAnotherThread(service.Refresh))

    monkeypatch.setattr(service, "Forget", crossed)
    assert adapter.LoadTournament(NEW_URL) is True
    _drain(analysis)

    assert answers == [STARTED, STARTED]
    assert old.calls == []
    progress = AnalysisService.Progress()
    assert (progress["status"], progress["done"], progress["total"]) \
        == ("done", 1, 1)
    assert service.Result()["url"] == NEW_URL
    assert _rows(service) == _expectedRows("one group, byes")


# ── two groups at a time ─────────────────────────────────────────────────

FIVE_GROUPS = (
    _phases(("Pools", [(61, "Pool 1"), (62, "Pool 2"), (63, "Pool 3"),
                       (64, "Pool 4"), (65, "Pool 5")])),
    {groupId: BYES for groupId in (61, 62, 63, 64, 65)})


def test_two_groups_are_fetched_at_a_time(service, analysis):  # noqa: F811
    """The first job fetches the phases and goes on with the queue; the
    second is started from the main thread (a job may not submit one).
    Never a third, and no job waits for another."""
    from src.core.TaskExecutor import JOB_THREAD_PREFIX
    provider = GatedProvider(*FIVE_GROUPS)
    _switchTournament(provider)
    try:
        assert service.Refresh() == STARTED
        _until(analysis, lambda: provider.inFlight == 2, "two groups asked")
        _spin(analysis)
        assert provider.inFlight == 2
        assert len(provider.asked) == 2
    finally:
        provider.release.set()
        _drain(analysis)

    assert provider.most == 2
    assert len(provider.threads) == 2
    assert all(name.startswith(JOB_THREAD_PREFIX) for name in provider.threads)
    assert provider.calls[0] == ["phases"]
    assert sorted(provider.calls[1:]) == [
        ["group", groupId] for groupId in (61, 62, 63, 64, 65)]
    progress = AnalysisService.Progress()
    assert (progress["status"], progress["done"], progress["total"],
            progress["failed"]) == ("done", 5, 5, 0)
    assert len(service.Rows()) == 5 * 7            # BYES: 7 matches
    # The second job: queued by the first, started on the main thread.
    kinds = [(kind, thread) for kind, thread, _, _ in service.calls]
    assert kinds[0] == ("fetcher", "MainThread")          # Refresh
    assert kinds[1][0] == "later"
    assert kinds[1][1].startswith(JOB_THREAD_PREFIX)
    assert kinds[2:] == [("fetcher", "MainThread")]


def test_a_single_group_takes_one_job(service, analysis):  # noqa: F811
    _switchTournament(_provider("one group, byes"))
    assert service.Refresh() == STARTED
    _drain(analysis)
    assert [kind for kind, *_ in service.calls] == ["fetcher"]


def test_a_dropped_fetchs_job_still_out_narrows_the_next_one(
        service, analysis):  # noqa: F811
    """While a dropped fetch's job still waits for its answer, the next
    fetch runs one job (2 less 1), not two: the match list never holds more
    than two of the job lane's threads (S4j review L1). Decided when its
    phases come."""
    old = GatedProvider(*SCENARIOS["one group, seeds known"])
    new = GatedProvider(*FIVE_GROUPS)
    _switchTournament(old)
    try:
        assert service.Refresh() == STARTED
        _until(analysis, lambda: old.inFlight == 1,
               "the first fetch's group asked")
        _switchTournament(new)
        assert service.Refresh() == STARTED
        _until(analysis, lambda: new.inFlight >= 1,
               "the new fetch's first group asked")
        _spin(analysis)
        assert new.inFlight == 1 and len(new.asked) == 1
        # The dropped job ends; the new fetch keeps its one job.
        old.release.set()
        _until(analysis, lambda: old.inFlight == 0, "the dropped job ended")
        _spin(analysis)
        assert new.inFlight == 1 and len(new.asked) == 1
    finally:
        old.release.set()
        new.release.set()
        _drain(analysis)

    assert new.most == 1 and len(new.threads) == 1
    progress = AnalysisService.Progress()
    assert (progress["status"], progress["done"], progress["total"]) \
        == ("done", 5, 5)
    assert len(service.Rows()) == 5 * 7
    # Two Refreshes, and no other job asked for.
    assert [kind for kind, *_ in service.calls] == ["fetcher", "fetcher"]


def test_a_group_listed_twice_is_fetched_once(service, analysis):  # noqa: F811
    """As the pre-S4j panel kept one result per group id (S4j review L8):
    one request, one group counted -- so no round prefix either."""
    phases = _phases(("Top 8", [(50, "Pool 1")]),
                     ("Top 8 again", [(50, "Pool 1")]))
    provider = FakeProvider(phases, {50: BYES})
    _switchTournament(provider)
    assert service.Refresh() == STARTED
    _drain(analysis)
    assert provider.calls == [["phases"], ["group", 50]]
    progress = AnalysisService.Progress()
    assert (progress["status"], progress["done"], progress["total"]) \
        == ("done", 1, 1)
    assert _rows(service) == _expectedRows("one group, byes")


# ── what goes wrong ──────────────────────────────────────────────────────

class _RaisingPhases(FakeProvider):
    def GetTournamentPhases(self, progress_callback=None, cancel_event=None,
                            strict=False):
        self.calls.append(["phases"])
        raise RuntimeError("start.gg is down")


class _RaisingGroup(FakeProvider):
    def GetAnalysisPhaseGroup(self, id, progress_callback=None,
                              cancel_event=None, strict=False):
        if id == 42:
            self.calls.append(["group", id])
            raise RuntimeError("timed out")
        return super().GetAnalysisPhaseGroup(id, strict=strict)


def test_a_phases_request_that_fails_is_a_failed_fetch(
        service, analysis):  # noqa: F811
    _switchTournament(_RaisingPhases(*SCENARIOS["one group, byes"]))
    assert service.Refresh() == STARTED
    _drain(analysis)
    progress = AnalysisService.Progress()
    assert (progress["status"], progress["error"]) == ("error",
                                                       "fetch_failed")
    assert not service.Running()


def test_a_group_request_that_fails_is_a_failed_group(
        service, analysis):  # noqa: F811
    phases = _phases(("Pools", [(41, "Pool 1"), (42, "Pool 2")]))
    _switchTournament(_RaisingGroup(phases, {41: BYES}))
    assert service.Refresh() == STARTED
    _drain(analysis)
    progress = AnalysisService.Progress()
    assert (progress["status"], progress["done"], progress["total"],
            progress["failed"]) == ("done", 2, 2, 1)
    assert len(service.Rows()) == 7


def test_a_job_that_raises_ends_the_fetch(
        service, analysis, monkeypatch):  # noqa: F811
    """Not left in progress: every later Refresh would answer BUSY."""
    from src.core import AnalysisService as module

    def broken(*args, **kwargs):
        raise RuntimeError("a bracket bug")

    _switchTournament(_provider("one group, byes"))
    monkeypatch.setattr(module, "BuildRows", broken)
    assert service.Refresh() == STARTED
    _drain(analysis)
    progress = AnalysisService.Progress()
    assert (progress["status"], progress["error"]) == ("error",
                                                       "fetch_failed")
    assert not service.Running()
    monkeypatch.undo()
    assert service.Refresh() == STARTED
    _drain(analysis)
    assert AnalysisService.Progress()["status"] == "done"


def test_a_group_whose_rows_raise_is_a_failed_group(
        service, analysis, monkeypatch):  # noqa: F811
    """Only that group's rows are lost (S4j review L2): the others stay,
    and the fetch is done."""
    from src.core import AnalysisService as module
    real = module.BracketRows

    def rows(bracket, entrants, prefix, groupId):
        if groupId == 42:
            raise RuntimeError("a bracket bug")
        return real(bracket, entrants, prefix, groupId)

    monkeypatch.setattr(module, "BracketRows", rows)
    phases = _phases(("Pools", [(41, "Pool 1"), (42, "Pool 2")]))
    _switchTournament(FakeProvider(phases, {41: BYES, 42: BYES}))
    assert service.Refresh() == STARTED
    _drain(analysis)
    progress = AnalysisService.Progress()
    assert (progress["status"], progress["done"], progress["total"],
            progress["failed"]) == ("done", 2, 2, 1)
    assert len(service.Rows()) == 7
    assert {row["roundOrder"][0] for row in service.Rows()} == {"41"}


def test_an_end_whose_publish_raised_is_published_again(
        service, analysis, monkeypatch):  # noqa: F811
    """The fetch was over when the write of its end raised: that end is
    written again, not left running in the state (S4j review L3)."""
    real = AnalysisService._Set
    refused = []

    def flaky(value):
        if value["status"] == "done" and not refused:
            refused.append(value)
            raise RuntimeError("the state could not be written")
        real(value)

    monkeypatch.setattr(service, "_Set", flaky)
    _switchTournament(_provider("one group, byes"))
    assert service.Refresh() == STARTED
    _drain(analysis)
    assert refused
    progress = AnalysisService.Progress()
    assert progress == dict(refused[0])
    assert progress["status"] == "done"
    assert not service.Running()
    assert len(service.Rows()) == 7
    assert service.Refresh() == STARTED
    _drain(analysis)


def test_a_refresh_that_raises_is_a_failed_fetch(
        service, analysis, webApp, monkeypatch):  # noqa: F811
    """The fetcher raised (nothing was started): Refresh raises, as it
    did, with nothing left in progress and nothing published; the route
    answers 500, which the Statistics page tells as a failed fetch (S4j
    review L10; the page's jest test)."""
    def broken(job, status):
        raise RuntimeError("the executor is gone")

    _switchTournament(_provider("one group, byes"))
    before = StateManager.Get("analysis")
    monkeypatch.setattr(service, "_fetcher", broken)
    with pytest.raises(RuntimeError):
        service.Refresh()
    assert not service.Running()
    assert StateManager.Get("analysis") == before

    answer = webApp.test_client().post("/api/v1/analysis:refresh")
    assert answer.status_code == 500
    assert answer.get_json()["error"]["code"] == "INTERNAL"
    assert not service.Running()
    assert StateManager.Get("analysis") == before


# ── the real provider, asked strictly ────────────────────────────────────

REAL_URL = "https://www.start.gg/tournament/real/event/analysis"


def _phasesAnswer(*groupIds):
    """TournamentPhasesQuery's answer: one phase with these groups."""
    return {"data": {"event": {"phases": [{
        "id": 7, "name": "Pools",
        "phaseGroups": {"nodes": [
            {"id": groupId, "displayIdentifier": str(index + 1),
             "bracketType": "DOUBLE_ELIMINATION"}
            for index, groupId in enumerate(groupIds)]}}]}}}


# AnalysisPhaseGroupQuery's answer for a group with no sets.
EMPTY_GROUP = {"data": {"phaseGroup": {
    "seeds": {"nodes": []}, "sets": {"nodes": []}, "progressionsOut": []}}}


def _realProvider(monkeypatch, phases, groups=None):
    """A StartGGDataProvider whose requests answer `phases` (to the phases
    query) and `groups[id]` -- an exception is raised. {} is what
    QueryRequests answers once its retries are spent."""
    from src.TournamentDataProvider.StartGGDataProvider import (
        StartGGDataProvider)
    provider = StartGGDataProvider(REAL_URL, None)

    def query(*args, **kwargs):
        params = kwargs["jsonParams"]
        if params["operationName"] == "TournamentPhasesQuery":
            answer = phases
        else:
            answer = (groups or {})[params["variables"]["id"]]
        if isinstance(answer, Exception):
            raise answer
        return copy.deepcopy(answer)

    monkeypatch.setattr(provider, "QueryRequests", query)
    return provider


@pytest.mark.parametrize("answer", [
    {}, RuntimeError("timed out"), {"data": {"event": None}}],
    ids=["no answer", "raised", "no event"])
def test_the_real_providers_failed_phases_request(
        service, analysis, monkeypatch, answer):  # noqa: F811
    """Asked strictly, it raises; the fetch is `fetch_failed`, not a
    tournament with no phase group (S4j review M1). Asked as before (the
    adapter's signal, Top 8), it still answers []."""
    provider = _realProvider(monkeypatch, answer)
    assert provider.GetTournamentPhases() == []
    with pytest.raises(Exception):
        provider.GetTournamentPhases(strict=True)

    _switchTournament(provider)
    assert service.Refresh() == STARTED
    _drain(analysis)
    progress = AnalysisService.Progress()
    assert (progress["status"], progress["error"], progress["total"]) \
        == ("error", "fetch_failed", 0)


def test_the_real_providers_tournament_with_no_phase_group(
        service, analysis, monkeypatch):  # noqa: F811
    provider = _realProvider(monkeypatch, {"data": {"event": {"phases": []}}})
    assert provider.GetTournamentPhases(strict=True) == []
    _switchTournament(provider)
    assert service.Refresh() == STARTED
    _drain(analysis)
    progress = AnalysisService.Progress()
    assert (progress["status"], progress["error"]) == ("error", "no_phases")


def test_the_real_providers_failed_group_requests(
        service, analysis, monkeypatch):  # noqa: F811
    """A group whose request failed ({} or an exception) did not answer;
    one with no sets did. Every group failed: `fetch_failed`. Asked as
    before, a failed group still answers the empty shape."""
    provider = _realProvider(monkeypatch, _phasesAnswer(41, 42),
                             {41: {}, 42: RuntimeError("timed out")})
    assert provider.GetAnalysisPhaseGroup(41) == {
        "entrants": [], "sets": {}, "progressionsOut": []}
    assert provider.GetAnalysisPhaseGroup(42)["sets"] == {}
    for groupId in (41, 42):
        with pytest.raises(Exception):
            provider.GetAnalysisPhaseGroup(groupId, strict=True)

    _switchTournament(provider)
    assert service.Refresh() == STARTED
    _drain(analysis)
    progress = AnalysisService.Progress()
    assert (progress["status"], progress["error"], progress["done"],
            progress["total"], progress["failed"]) \
        == ("error", "fetch_failed", 2, 2, 2)

    # One group with no sets answered: done, no match, both counted.
    provider = _realProvider(monkeypatch, _phasesAnswer(41, 42),
                             {41: EMPTY_GROUP, 42: {}})
    assert provider.GetAnalysisPhaseGroup(41, strict=True)["sets"] == {}
    _switchTournament(provider)
    assert service.Refresh() == STARTED
    _drain(analysis)
    progress = AnalysisService.Progress()
    assert (progress["status"], progress["done"], progress["total"],
            progress["failed"]) == ("done", 2, 2, 2)
    assert service.Rows() == []


# ── the locks ────────────────────────────────────────────────────────────

def test_nothing_is_waited_on_inside_the_locks(service, analysis):  # noqa: F811
    """Lock order: StateManager.lock, then the service's, which is held
    only around its own fields -- never while the state is read or
    written, the fetcher or `later` is called. Nothing waits on another
    thread (agent-pitfalls #17): no run_sync."""
    from src.Helpers import AutonaQtHelper
    executor = AutonaQtHelper.gui_executor
    runSync = executor.run_sync
    waits = []
    insideOwn = []
    realSet, realGet = StateManager.Set, StateManager.Get

    def _runSync(fn):
        waits.append(threading.current_thread().name)
        return runSync(fn)

    def _set(key, value=None, *args, **kwargs):
        if service._lock._is_owned():
            insideOwn.append(("Set", key))
        return realSet(key, value, *args, **kwargs)

    def _get(key, default=None):
        if service._lock._is_owned():
            insideOwn.append(("Get", key))
        return realGet(key, default)

    label = "several groups"
    _switchTournament(_provider(label))
    with patch.object(executor, "run_sync", _runSync), \
            patch.object(StateManager, "Set", _set), \
            patch.object(StateManager, "Get", _get):
        assert service.Refresh() == STARTED
        _drain(analysis)
        _switchTournament(_provider(label))
        _drain(analysis)
        assert service.Refresh() == STARTED
        _drain(analysis)
    assert _rows(service) == _expectedRows(label)
    assert waits == []
    assert insideOwn == []
    assert service.calls and not [
        call for call in service.calls if call[2] or call[3]], service.calls


# ── the adapter, the seed ────────────────────────────────────────────────

def test_the_adapter_has_no_callback_style_fetch():
    """The provider adapter's callback-style fetch is gone: core asks the
    provider itself, on a job. (The Qt panel that called it went with
    S4j's thinning and S4k's removal; the Statistics page asks core
    through /api/v1.)"""
    from src.AutonaTournamentDataProvider import AutonaTournamentDataProvider
    assert not hasattr(AutonaTournamentDataProvider, "GetAnalysisPhaseGroup")
    assert not (PROJECT_ROOT / "src" / "AutonaAnalysisWidget.py").exists()


def test_the_launch_seed_is_idle_at_the_revision_published_last(
        analysis):  # noqa: F811
    """A fetch is never running at launch (its rows died with the last
    process); the revision goes on from the one saved, so that a reader
    holding an older one sees the change."""
    StateManager.Set("analysis", {"status": "running", "error": "",
                                  "done": 2, "total": 5, "failed": 0,
                                  "revision": 7})
    schema.SeedPanelState()
    assert StateManager.Get("analysis") == schema.DefaultAnalysis(7)
    StateManager.Set("analysis", "not a dict")
    schema.SeedPanelState()
    assert StateManager.Get("analysis") == schema.DefaultAnalysis(0)
    assert schema.KEY_CATALOG[schema.ANALYSIS_ROOT] == schema.ANALYSIS_KEYS
    assert tuple(schema.DefaultAnalysis()) == schema.ANALYSIS_KEYS


def test_the_round_prefix():
    assert RoundPrefix("Pools", "Pool A1") == "Pools / Pool A1 · "
    assert RoundPrefix("Pool B1 Finals", "Pool B1") == "Pool B1 Finals · "
    assert RoundPrefix("", "Pool C") == "Pool C · "
    assert RoundPrefix("Bracket", "") == "Bracket · "


def test_the_phase_groups_once_each_in_order():
    phases = _phases(("Pools", [(41, "Pool 1"), (None, "Pool X"),
                                (42, "Pool 2")]),
                     ("Top 8", [(42, "Top 8"), (50, "Top 8")]))
    assert PhaseGroupEntries(phases) == [
        ("Pools", 41, "Pool 1"), ("Pools", 42, "Pool 2"),
        ("Top 8", 50, "Top 8")]
    assert PhaseGroupEntries(None) == []


# ── the headless shell ───────────────────────────────────────────────────

from tests.test_bootstrap_split import _PRELUDE, _probe  # noqa: E402
from tests.test_provider_jobs import adapter, executor  # noqa: E402,F401

_HEADLESS_SCRIPT = _PRELUDE + r'''
import time

from qtpy.QtCore import QCoreApplication

app = QCoreApplication([])

from src.Helpers import AutonaQtHelper
AutonaQtHelper.init_gui_executor()
from src.bootstrap import bootstrap_core
bootstrap_core()

from src.AutonaTournamentDataProvider import AutonaTournamentDataProvider
from src.Helpers.AutonaLocaleHelper import AutonaLocaleHelper
from src.core.AnalysisService import AnalysisService
from src.core.StateManager import StateManager

AutonaLocaleHelper.LoadLocale()
AutonaLocaleHelper.LoadRoundNames()

with open(sys.argv[2], encoding="utf-8") as file:
    scenario = json.load(file)
groups = {int(key): value for key, value in scenario["groups"].items()}
calls = []
threads = set()
# The first two groups wait for each other: two jobs fetch at a time.
pair = threading.Barrier(2, timeout=20)
paired = []

class Provider:
    url = "https://www.start.gg/tournament/headless/event/analysis"

    def GetTournamentPhases(self, progress_callback=None, cancel_event=None,
                            strict=False):
        calls.append(["phases"])
        return json.loads(json.dumps(scenario["phases"]))

    def GetAnalysisPhaseGroup(self, id, progress_callback=None,
                              cancel_event=None, strict=False):
        calls.append(["group", id])
        threads.add(threading.current_thread().name)
        if len(calls) <= 3:
            pair.wait()
            paired.append(id)
        return json.loads(json.dumps(groups.get(id)))

AutonaTournamentDataProvider.instance.provider = Provider()
answer = AnalysisService.instance.Refresh()
deadline = time.monotonic() + 60
while AnalysisService.instance.Running() and time.monotonic() < deadline:
    app.processEvents()
    time.sleep(0.005)

report({
    "answer": answer,
    "progress": AnalysisService.Progress(),
    "state": StateManager.Get("analysis"),
    "result": AnalysisService.instance.Result(),
    "calls": calls,
    "threads": sorted(threads),
    "paired": sorted(paired),
})
'''


def test_the_headless_shell_fetches_the_match_list(tmp_path):
    """bootstrap_core alone, under a bare QCoreApplication (what the
    headless shell runs): the fetch runs, two groups at a time, and makes
    the recorded rows."""
    label = "several groups"
    phases, groups = SCENARIOS[label]
    # A file, not an argument: over the length a command line takes.
    scenario = tmp_path / "scenario.json"
    scenario.write_text(json.dumps({"phases": phases, "groups": groups}),
                        encoding="utf-8")
    result = _probe(tmp_path, _HEADLESS_SCRIPT, str(scenario))

    assert result["answer"] == STARTED
    assert result["progress"] == result["state"]
    assert {key: value for key, value in result["progress"].items()
            if key != "revision"} == EXPECTED_PROGRESS[label]
    assert result["result"]["url"] \
        == "https://www.start.gg/tournament/headless/event/analysis"
    assert result["result"]["revision"] == result["progress"]["revision"]
    rows = sorted((_pageCells(row) for row in result["result"]["rows"]),
                  key=lambda row: row[0][1])
    assert rows == _expectedRows(label)
    assert len(result["paired"]) == 2 and len(result["threads"]) == 2
    assert sorted(result["calls"], key=json.dumps) == sorted(
        [["phases"]] + [["group", groupId] for groupId in groups],
        key=json.dumps)
