"""The set selection's lists (ADR-005 S4v): core's SetSelectionService, its v1
routes, and the Qt windows' agreement with them.

What the Qt set selection and station windows fetch, for a page and a shell
with no window. Pinned here: reading never fetches; one fetch per list, another
query replacing the set list's and the same one refused; a failed request told
from an empty answer; what a stale answer does (nothing), across a replacement,
a cancel and a tournament change; the set list coming back with its tournament
as the window's cache does, the others not; the rows' whitelist; the access
rules; and the windows asking start.gg the same and showing the same.

The provider is faked at its interface (the real one's strict mode is
tests/test_startgg_parse.py's), on the real adapter's jobs. What a board then
does with a chosen set or station is tests/test_set_follow.py's.
"""
import inspect
import json
import threading

import pytest

from src import AutonaWebOperations as WebOps
from src.AutonaTournamentDataProvider import AutonaTournamentDataProvider
from src.core import SetSelectionService as selection
from src.core import StateSchema as schema
from src.core import WebAccess
from src.core.SetSelectionService import (
    ALL_PHASES, BUSY, NO_TOURNAMENT, PHASE_REQUIRED, PHASES, SETS, STARTED,
    STATIONS, UNKNOWN_PHASE, SetSelectionService)
from src.core.SettingsManager import SettingsManager
from src.core.StateManager import StateManager
from tests.test_analysis_rows import _drain
from tests.test_analysis_service import _OwnedLock, _until
from tests.test_api_v1 import webApp  # noqa: F401

WAIT = 10
URL = "https://www.start.gg/tournament/t/event/e"
OTHER_URL = "https://www.start.gg/tournament/other/event/e"

GROUPS = {"Pools": [101, 102], "Top 8": [103]}
PHASE_ANSWER = [
    {"id": 11, "name": "Pools", "groups": [
        {"id": 101, "name": "Pool A", "bracketType": "DOUBLE_ELIMINATION"},
        {"id": 102, "name": "Pool B", "bracketType": "DOUBLE_ELIMINATION"}]},
    {"id": 12, "name": "Top 8", "groups": [
        {"id": 103, "name": "", "bracketType": "DOUBLE_ELIMINATION"}]},
]
STATION_ANSWER = [
    {"id": 77, "identifier": 4, "type": "station", "stream": "channel"},
    {"id": 78, "identifier": 5, "type": "station", "stream": ""},
    {"id": 90, "identifier": "channel", "type": "stream"},
]


def _member(tag):
    # As the provider's parse makes a participant: much more than the list
    # shows, and some of it not for whoever can reach the port.
    return {"gamerTag": tag, "name": f"real name of {tag}",
            "twitter": f"handle_of_{tag}", "mains": {"ssbu": [["mario", 0]]},
            "id": [1, 2]}


def _set(setId, p1=("ALPHA",), p2=("BRAVO",), **fields):
    """A set as ParseMatchDataNewApi answers it. Doubles: several members."""
    answer = {
        "id": setId, "team1score": 0, "team2score": 0,
        "round_name": "Winners Final", "round_name_en": "Winners Final",
        "tournament_phase": "Pools", "tournament_pool": "Pool A",
        "bracket_type": "DOUBLE_ELIMINATION",
        "p1_name": p1[0] if p1 else "", "p2_name": p2[0] if p2 else "",
        "p1_seed": 1, "p2_seed": 2, "stream": "", "station": 3,
        "isOnline": False, "isPools": True, "round": 3,
        "entrants": [[_member(tag) for tag in p1],
                     [_member(tag) for tag in p2]],
    }
    answer.update(fields)
    return answer


class FakeTournament:
    """The loaded tournament, at the three methods the lists call. A kind's
    `answers`, `gates` and `cancels` are per call, in order; one with no answer
    of its own answers the last. `failing`: what a request does when it fails,
    as the real provider: it raises when strict, and answers empty when not."""

    def __init__(self, url=URL, phases=PHASE_ANSWER, sets=None,
                 stations=STATION_ANSWER):
        self.url = url
        self.answers = {PHASES: [phases], SETS: [sets or [_set(1)]],
                        STATIONS: [stations]}
        self.gates = {PHASES: [], SETS: [], STATIONS: []}
        self.asked = {PHASES: [], SETS: [], STATIONS: []}
        self.cancels = []
        self.failing = set()
        self.missingPages = 0
        self.pages = []
        self.locksHeld = []
        self._lock = threading.Lock()
        self._arrived = threading.Condition(self._lock)

    def _answer(self, kind, asked, strict):
        self.locksHeld.append(StateManager.lock._is_owned())
        with self._arrived:
            number = len(self.asked[kind])
            self.asked[kind].append(asked)
            self._arrived.notify_all()
            gate = self.gates[kind][number] \
                if number < len(self.gates[kind]) else None
        if gate is not None:
            assert gate.wait(WAIT), "the test never released it"
        if kind in self.failing:
            if strict:
                raise LookupError("start.gg did not answer")
            return []
        answers = self.answers[kind]
        return answers[min(number, len(answers) - 1)]

    def waitAsked(self, kind, count=1):
        with self._arrived:
            assert self._arrived.wait_for(
                lambda: len(self.asked[kind]) >= count, WAIT), \
                f"{kind} never asked {count} times"

    def GetTournamentPhases(self, progress_callback=None, cancel_event=None,
                            strict=False):
        return self._answer(PHASES, {"strict": strict}, strict)

    def GetStations(self, progress_callback=None, cancel_event=None,
                    strict=False):
        return self._answer(STATIONS, {"strict": strict}, strict)

    def GetMatches(self, getFinished=False, phaseGroupIds=None,
                   progress_callback=None, cancel_event=None, strict=False,
                   failed_pages=None):
        self.cancels.append(cancel_event)
        for done, total in self.pages:
            progress_callback(done, total)
        answer = self._answer(
            SETS, {"finished": getFinished, "groups": phaseGroupIds}, strict)
        if self.missingPages and failed_pages is not None:
            failed_pages(self.missingPages)
        return answer


@pytest.fixture
def service(autonaApp, monkeypatch):
    """A SetSelectionService of the test's own, wired as bootstrap_core wires
    the session's, in its place; it records what locks its fetcher is called
    with. No tournament left behind, nor anything of its in the state."""
    adapter = AutonaTournamentDataProvider.instance
    saved = adapter.provider
    kept = StateManager.Get(schema.SET_SELECTION_ROOT)

    def fetcher(job, status):
        made.fetcherLocks.append((StateManager.lock._is_owned(),
                                  made._lock._is_owned()))
        return adapter.RunWithProvider(job, status)

    made = SetSelectionService(fetcher=fetcher)
    made._lock = _OwnedLock()
    made.fetcherLocks = []
    monkeypatch.setattr(SetSelectionService, "instance", made)
    adapter.provider = None
    try:
        yield made
    finally:
        made.Forget()
        _drain(autonaApp)
        adapter.provider = saved
        if kept is None:
            StateManager.Unset(schema.SET_SELECTION_ROOT)
        else:
            StateManager.Set(schema.SET_SELECTION_ROOT, kept)
        autonaApp.settle()


def _load(service, provider):
    """`provider` loaded, as the adapter's Load / UnloadTournament leave it."""
    AutonaTournamentDataProvider.instance.provider = provider
    service.Forget()


def _progress(kind):
    return SetSelectionService.Progress(kind)


def _fetched(service, autonaApp, provider, kind=PHASES):
    assert {PHASES: service.RefreshPhases,
            STATIONS: service.RefreshStations}[kind]() == STARTED
    _drain(autonaApp)
    assert _progress(kind)["status"] == "done"


def _ids(service):
    return [row["id"] for row in service.Result(SETS)["rows"]]


# ── one fetch per list ───────────────────────────────────────────────────

@pytest.mark.parametrize("kind, refresh", [
    (PHASES, lambda service: service.RefreshPhases()),
    (STATIONS, lambda service: service.RefreshStations()),
    (SETS, lambda service: service.RefreshSets(None, False))])
def test_a_fetch_is_published_and_its_rows_kept(
        service, autonaApp, kind, refresh):
    provider = FakeTournament(phases=[], stations=STATION_ANSWER)
    release = threading.Event()
    provider.gates[kind] = [release]
    _load(service, provider)
    before = _progress(kind)["revision"]

    assert refresh(service) == STARTED
    # Published before Refresh returned.
    assert _progress(kind) == dict(schema.DefaultSetSelectionFetch(before + 1),
                                   status="running")
    assert service.Running(kind)
    assert service.Result(kind)["rows"] == []
    release.set()
    _drain(autonaApp)

    assert _progress(kind) == dict(schema.DefaultSetSelectionFetch(before + 1),
                                   status="done")
    assert not service.Running(kind)
    result = service.Result(kind)
    assert result["url"] == URL
    assert isinstance(result["fetchedAt"], int)
    assert result["revision"] == before + 1
    assert all(asked.get("strict", True) for asked in provider.asked[PHASES])


@pytest.mark.parametrize("kind, refresh", [
    (PHASES, lambda service: service.RefreshPhases()),
    (STATIONS, lambda service: service.RefreshStations())])
def test_a_refresh_while_one_runs_is_busy(service, autonaApp, kind, refresh):
    provider = FakeTournament()
    release = threading.Event()
    provider.gates[kind] = [release]
    _load(service, provider)
    assert refresh(service) == STARTED
    provider.waitAsked(kind)
    published = _progress(kind)

    assert refresh(service) == BUSY
    assert _progress(kind) == published
    release.set()
    _drain(autonaApp)
    assert len(provider.asked[kind]) == 1
    assert _progress(kind)["status"] == "done"

    assert refresh(service) == STARTED     # once it is over
    _drain(autonaApp)


def test_the_same_query_while_it_runs_is_busy(service, autonaApp):
    provider = FakeTournament()
    release = threading.Event()
    provider.gates[SETS] = [release]
    _load(service, provider)
    assert service.RefreshSets(None, False) == STARTED
    provider.waitAsked(SETS)
    published = _progress(SETS)

    assert service.RefreshSets(None, False) == BUSY
    assert _progress(SETS) == published
    assert not provider.cancels[0].is_set()
    release.set()
    _drain(autonaApp)
    assert len(provider.asked[SETS]) == 1


@pytest.mark.parametrize("firstAnswersFirst", [True, False])
def test_another_query_stops_the_one_running_and_replaces_it(
        service, autonaApp, firstAnswersFirst):
    """As the window's phase combo and checkbox do while it fetches. The one
    replaced is told to stop, and whatever it still answers, in either order,
    is dropped."""
    provider = FakeTournament()
    first, second = threading.Event(), threading.Event()
    provider.gates[SETS] = [first, second]
    provider.answers[SETS] = [[_set(1)], [_set(2)]]
    _load(service, provider)
    before = _progress(SETS)["revision"]

    assert service.RefreshSets(None, False) == STARTED
    provider.waitAsked(SETS)
    assert service.RefreshSets(None, True) == STARTED
    provider.waitAsked(SETS, 2)
    assert provider.cancels[0].is_set()
    assert not provider.cancels[1].is_set()
    assert _progress(SETS)["revision"] == before + 2

    for gate in ((first, second) if firstAnswersFirst else (second, first)):
        gate.set()
        _drain(autonaApp) if gate is second else None
    _drain(autonaApp)

    assert _ids(service) == [2]
    assert service.Result(SETS)["query"] == {"phase": ALL_PHASES,
                                             "finished": True}
    assert _progress(SETS)["status"] == "done"
    assert _progress(SETS)["revision"] == before + 2


def test_a_stopped_fetch_still_out_counts_against_the_job_lane(
        service, autonaApp):
    """The job lane's four threads are every tournament fetch's: a third query
    while two requests are out is refused, not started on a third thread."""
    provider = FakeTournament()
    first, second = threading.Event(), threading.Event()
    provider.gates[SETS] = [first, second]
    _load(service, provider)
    assert service.RefreshSets(None, False) == STARTED
    provider.waitAsked(SETS)
    assert service.RefreshSets(None, True) == STARTED
    provider.waitAsked(SETS, 2)
    published = _progress(SETS)

    assert service.RefreshSets(ALL_PHASES, False) == BUSY
    assert _progress(SETS) == published
    assert not provider.cancels[1].is_set()     # the one wanted goes on

    first.set()
    _until(autonaApp, lambda: service._alive[SETS] < selection.MAX_SET_JOBS,
           "the stopped job over")
    assert service.RefreshSets(ALL_PHASES, False) == STARTED
    second.set()
    _drain(autonaApp)
    assert service.Result(SETS)["query"] == {"phase": "all", "finished": False}


def test_a_cancelled_fetch_still_out_counts_against_the_job_lane(
        service, autonaApp):
    """A cancel leaves no fetch current, and its request out: refresh, cancel,
    refresh, cancel... filled the lane, one job more each time."""
    provider = FakeTournament(phases=[])
    first, second, third = (threading.Event() for _ in range(3))
    provider.gates[SETS] = [first, second, third]
    _load(service, provider)
    for out in (1, 2):
        assert service.RefreshSets(None, False) == STARTED
        provider.waitAsked(SETS, out)
        assert service.CancelSets() is True
    published = _progress(SETS)

    assert service.RefreshSets(None, False) == BUSY
    assert service._alive[SETS] == selection.MAX_SET_JOBS
    assert len(provider.asked[SETS]) == 2
    assert _progress(SETS) == published
    assert not service.Running(SETS)

    first.set()
    _until(autonaApp, lambda: service._alive[SETS] < selection.MAX_SET_JOBS,
           "the cancelled job over")
    assert service.RefreshSets(None, False) == STARTED
    second.set()
    third.set()
    _drain(autonaApp)
    assert _progress(SETS)["status"] == "done"
    assert not service._alive


@pytest.mark.parametrize("kind, refresh", [
    (PHASES, lambda service: service.RefreshPhases()),
    (STATIONS, lambda service: service.RefreshStations()),
    (SETS, lambda service: service.RefreshSets(None, False))])
def test_a_forgotten_fetch_still_out_counts_against_the_job_lane(
        service, autonaApp, kind, refresh):
    """A tournament changed leaves no fetch current either, and the phases' and
    stations' requests cannot be told to stop. The next tournament's list starts
    all the same; only the next one is refused."""
    provider = FakeTournament(phases=[])
    first, second = threading.Event(), threading.Event()
    provider.gates[kind] = [first, second]
    _load(service, provider)
    for out in (1, 2):
        assert refresh(service) == STARTED
        provider.waitAsked(kind, out)
        _load(service, provider)

    assert refresh(service) == BUSY
    assert service._alive[kind] == selection.MAX_SET_JOBS
    assert not service.Running(kind)

    first.set()
    second.set()
    _drain(autonaApp)
    assert refresh(service) == STARTED
    _drain(autonaApp)
    assert _progress(kind)["status"] == "done"


@pytest.mark.parametrize("failing", [False, True])
def test_a_job_over_before_its_refresh_published_it(
        service, autonaApp, failing):
    """The job can end before its Refresh gets to publish it. Nothing of it goes
    out before that (with no revision it has no place in the list's), and the
    Refresh then publishes it as it is: one write, its end, with its rows."""
    provider = FakeTournament(phases=[])
    if failing:
        provider.failing.add(SETS)
    _load(service, provider)
    before = _progress(SETS)["revision"]
    service._fetcher = lambda job, status: (job(provider), True)[1]
    written = []
    real = SetSelectionService._Set

    def recorded(kind, value):
        written.append((kind, value))
        real(kind, value)

    with pytest.MonkeyPatch.context() as patching:
        patching.setattr(SetSelectionService, "_Set", staticmethod(recorded))
        assert service.RefreshSets(None, False) == STARTED

    expected = schema.DefaultSetSelectionFetch(before + 1)
    if failing:
        expected.update(status="error", error="fetch_failed")
    else:
        expected.update(status="done")
    assert written == [(SETS, expected)]
    assert _progress(SETS) == expected
    assert _ids(service) == ([] if failing else [1])


# ── what a set list is asked for ─────────────────────────────────────────

@pytest.mark.parametrize("method, asked", [
    ("GetTournamentPhases", {"strict"}),
    ("GetStations", {"strict"}),
    ("GetMatches", {"strict", "failed_pages"})])
def test_the_base_provider_says_what_the_lists_ask_with(method, asked):
    """The base class is the providers' interface, and a provider that goes by
    it would answer the lists' calls with a TypeError."""
    from src.TournamentDataProvider.TournamentDataProvider import (
        TournamentDataProvider)

    parameters = inspect.signature(
        getattr(TournamentDataProvider, method)).parameters
    assert asked <= set(parameters)
    assert all(parameters[name].default in (False, None) for name in asked)


@pytest.mark.parametrize("phase, finished, groups, query", [
    (ALL_PHASES, False, [101, 102, 103], ALL_PHASES),
    (11, False, [101, 102], 11),
    # The phase as the phases have it, however it was named.
    ("12", True, [103], 12),
])
def test_a_phase_is_asked_for_by_its_groups(
        service, autonaApp, phase, finished, groups, query):
    provider = FakeTournament()
    _load(service, provider)
    _fetched(service, autonaApp, provider)

    assert service.RefreshSets(phase, finished) == STARTED
    _drain(autonaApp)
    assert provider.asked[SETS] == [{"finished": finished, "groups": groups}]
    assert service.Result(SETS)["query"] == {"phase": query,
                                             "finished": finished}


@pytest.mark.parametrize("phases, first, second, query", [
    (PHASE_ANSWER, 11, "11", 11),
    (PHASE_ANSWER, "11", 11, 11),
    # No phases known: asked for the whole event either way.
    ([], None, ALL_PHASES, ALL_PHASES),
    ([], ALL_PHASES, None, ALL_PHASES),
])
def test_the_same_request_is_the_same_query(
        service, autonaApp, phases, first, second, query):
    """Named another way it replaced the fetch with the very same one (a
    request thrown away, and its job on the lane), and read back two ways."""
    provider = FakeTournament(phases=phases)
    release = threading.Event()
    provider.gates[SETS] = [release]
    _load(service, provider)
    _fetched(service, autonaApp, provider)

    assert service.RefreshSets(first, False) == STARTED
    provider.waitAsked(SETS)
    assert service.RefreshSets(second, False) == BUSY
    assert not provider.cancels[0].is_set()
    release.set()
    _drain(autonaApp)
    assert len(provider.asked[SETS]) == 1
    assert service.Result(SETS)["query"] == {"phase": query, "finished": False}


def test_a_tournament_change_cannot_come_between_the_phases_and_the_fetch(
        service, autonaApp):
    """A set list goes by the phases it read. Read apart from making the fetch
    current, a change between the two left the fetch current, asking the next
    tournament for the last one's groups: a wrong `done`, and no rows. A Refresh
    is a request's thread and the change the main thread's, so it is staged in
    the read, where it gets all the time it wants: it has to wait, and then
    drops the fetch."""
    last = FakeTournament()
    _load(service, last)
    _fetched(service, autonaApp, last)
    following = FakeTournament(url=OTHER_URL, phases=[])
    changes = []

    class Results(dict):
        def get(self, kind, default=None):
            answer = dict.get(self, kind, default)
            if kind == PHASES and not changes:
                changes.append(threading.Thread(
                    target=_load, args=(service, following)))
                changes[0].start()
                changes[0].join(0.5)
            return answer

    service._results = Results(service._results)
    assert service.RefreshSets(11, False) == STARTED
    changes[0].join(WAIT)
    assert not changes[0].is_alive()
    _drain(autonaApp)

    assert _progress(SETS)["status"] == "idle"
    assert service.Result(SETS)["rows"] == []
    assert not service.Running(SETS)

    # What the next one asks is the next tournament's.
    assert service.RefreshSets(11, False) == UNKNOWN_PHASE
    assert service.RefreshSets(None, False) == STARTED
    _drain(autonaApp)
    assert following.asked[SETS][-1] == {"finished": False, "groups": None}
    assert _progress(SETS)["status"] == "done"
    assert service.Result(SETS)["url"] == OTHER_URL


def test_while_the_phases_are_known_a_phase_must_be_chosen(service, autonaApp):
    """The window's "Select a phase": fetching a large event's every phase is
    what choosing one avoids, and "all" is there for it. Nothing is asked and
    nothing published."""
    provider = FakeTournament()
    _load(service, provider)
    _fetched(service, autonaApp, provider)
    published = _progress(SETS)

    assert service.RefreshSets(None, False) == PHASE_REQUIRED
    assert service.RefreshSets(999, False) == UNKNOWN_PHASE
    assert service.Running(SETS) is False
    assert _progress(SETS) == published
    assert provider.asked[SETS] == []


def test_with_no_phases_known_the_whole_event_is_fetched(service, autonaApp):
    """None known (not fetched, or an event with none): the window hides its
    selector and fetches without a restriction. Nothing to name by id."""
    provider = FakeTournament(phases=[])
    _load(service, provider)

    assert service.RefreshSets(11, False) == UNKNOWN_PHASE
    assert service.RefreshSets(None, False) == STARTED
    _drain(autonaApp)
    _fetched(service, autonaApp, provider)      # none: done, and still none
    assert service.Result(PHASES)["rows"] == []
    assert service.RefreshSets(ALL_PHASES, True) == STARTED
    _drain(autonaApp)
    assert provider.asked[SETS] == [{"finished": False, "groups": None},
                                    {"finished": True, "groups": None}]


def test_a_phase_with_no_groups_asks_as_the_window_does(service, autonaApp):
    # An empty restriction is none (GetMatches), for the window too.
    provider = FakeTournament(phases=[{"id": 11, "name": "Pools"}])
    _load(service, provider)
    _fetched(service, autonaApp, provider)
    assert service.RefreshSets(11, False) == STARTED
    _drain(autonaApp)
    assert provider.asked[SETS] == [{"finished": False, "groups": []}]


# ── a failed request is not an empty answer ──────────────────────────────

@pytest.mark.parametrize("kind, refresh, empty", [
    (PHASES, lambda service: service.RefreshPhases(), {"phases": []}),
    (STATIONS, lambda service: service.RefreshStations(), {"stations": []}),
    (SETS, lambda service: service.RefreshSets(ALL_PHASES, False),
     {"sets": []})])
def test_a_failed_fetch_is_told_from_an_empty_answer(
        service, autonaApp, kind, refresh, empty):
    provider = FakeTournament()
    _load(service, provider)
    assert refresh(service) == STARTED
    _drain(autonaApp)
    rows = service.Result(kind)["rows"]
    assert rows

    # Failed: an error, and the rows of the last one that did not.
    provider.failing.add(kind)
    assert refresh(service) == STARTED
    _drain(autonaApp)
    progress = _progress(kind)
    assert (progress["status"], progress["error"]) == ("error", "fetch_failed")
    assert service.Result(kind)["rows"] == rows
    assert not service.Running(kind)

    # Empty: done, no error, no rows.
    provider.failing.clear()
    provider.answers[kind] = [[]]
    assert refresh(service) == STARTED
    _drain(autonaApp)
    progress = _progress(kind)
    assert (progress["status"], progress["error"]) == ("done", "")
    assert service.Result(kind)["rows"] == []


def test_what_is_missing_from_a_set_list_is_counted(service, autonaApp):
    """Pages that could not be fetched, and a set the parse could not read (it
    answers {}): a done, with what it has. The window shows them no differently
    from sets that are not there."""
    provider = FakeTournament(phases=[], sets=[_set(1), {}, _set(2)])
    provider.missingPages = 2
    _load(service, provider)
    assert service.RefreshSets(None, False) == STARTED
    _drain(autonaApp)
    assert _ids(service) == [1, 2]
    progress = _progress(SETS)
    assert (progress["status"], progress["failed"]) == ("done", 3)

    provider.missingPages = 0
    provider.answers[SETS] = [[_set(1)]]
    assert service.RefreshSets(None, False) == STARTED
    _drain(autonaApp)
    assert _progress(SETS)["failed"] == 0


def test_the_pages_are_published_as_they_come(service, autonaApp):
    provider = FakeTournament(phases=[])
    release = threading.Event()
    provider.gates[SETS] = [release]
    provider.pages = [(1, 0), (1, 3), (2, 3)]
    _load(service, provider)
    revision = _progress(SETS)["revision"]
    assert service.RefreshSets(None, False) == STARTED
    provider.waitAsked(SETS)
    _until(autonaApp, lambda: _progress(SETS)["done"] == 2, "the second page")
    assert _progress(SETS) == dict(
        schema.DefaultSetSelectionFetch(revision + 1), status="running",
        done=2, total=3)
    release.set()
    _drain(autonaApp)
    assert _progress(SETS)["status"] == "done"


def test_an_event_with_no_pages_says_no_total(service, autonaApp):
    # The first page of an event with no sets reports 1 of 0.
    provider = FakeTournament(phases=[], sets=None)
    provider.answers[SETS] = [[]]
    provider.pages = [(1, 0)]
    _load(service, provider)
    assert service.RefreshSets(None, False) == STARTED
    _drain(autonaApp)
    progress = _progress(SETS)
    assert (progress["done"], progress["total"]) == (0, 0)


# ── a stale answer does nothing ──────────────────────────────────────────

def test_a_cancel_stops_the_fetch_and_leaves_the_rows(service, autonaApp):
    """Choosing a set in the window stops the list still loading."""
    provider = FakeTournament(phases=[])
    provider.answers[SETS] = [[_set(1)], [_set(2)]]
    _load(service, provider)
    assert service.CancelSets() is False
    assert service.RefreshSets(None, False) == STARTED
    _drain(autonaApp)
    assert _ids(service) == [1]

    release = threading.Event()
    provider.gates[SETS] = [None, release]
    assert service.RefreshSets(None, True) == STARTED
    provider.waitAsked(SETS, 2)
    revision = _progress(SETS)["revision"]

    assert service.CancelSets() is True
    assert provider.cancels[1].is_set()
    assert _progress(SETS) == schema.DefaultSetSelectionFetch(revision + 1)
    assert not service.Running(SETS)
    assert service.CancelSets() is False

    release.set()
    _drain(autonaApp)
    assert _ids(service) == [1]
    assert _progress(SETS) == schema.DefaultSetSelectionFetch(revision + 1)


@pytest.mark.parametrize("kind, refresh", [
    (PHASES, lambda service: service.RefreshPhases()),
    (STATIONS, lambda service: service.RefreshStations()),
    (SETS, lambda service: service.RefreshSets(None, False))])
def test_a_tournament_change_drops_the_fetch_in_progress(
        service, autonaApp, kind, refresh):
    provider = FakeTournament(phases=[])
    release = threading.Event()
    provider.gates[kind] = [release]
    _load(service, provider)
    assert refresh(service) == STARTED
    provider.waitAsked(kind)
    revision = _progress(kind)["revision"]

    next_ = FakeTournament(url=OTHER_URL, phases=[])
    _load(service, next_)
    assert not service.Running(kind)
    assert _progress(kind) == schema.DefaultSetSelectionFetch(revision + 1)
    if kind == SETS:
        assert provider.cancels[0].is_set()

    release.set()
    _drain(autonaApp)
    assert service.Result(kind)["rows"] == []
    assert _progress(kind) == schema.DefaultSetSelectionFetch(revision + 1)

    # One asked for after it fetches from the new tournament.
    assert refresh(service) == STARTED
    _drain(autonaApp)
    assert service.Result(kind)["url"] == OTHER_URL


def test_the_lists_are_read_only_for_the_tournament_loaded_now(
        service, autonaApp):
    """The window's cache: tied to the URL, kept through a tournament change,
    back when the same tournament is loaded again. The phases and the stations
    are asked for again at every load and every open, so they are dropped."""
    first = FakeTournament()
    _load(service, first)
    _fetched(service, autonaApp, first)
    _fetched(service, autonaApp, first, STATIONS)
    assert service.RefreshSets(12, True) == STARTED
    _drain(autonaApp)
    assert WebOps.get_tournament_sets()["url"] == URL

    # Another tournament: nothing of this one.
    _load(service, FakeTournament(url=OTHER_URL))
    nothing = {"status": "idle", "url": "", "fetchedAt": None, "rows": []}
    assert WebOps.get_tournament_sets() == dict(nothing, query=None)
    assert WebOps.get_tournament_phases() == nothing
    assert WebOps.get_tournament_stations() == nothing

    # None loaded: the same.
    _load(service, None)
    assert WebOps.get_tournament_sets() == dict(nothing, query=None)

    # The same tournament again -- a forced re-fetch is a new provider too: the
    # set list and what it was asked for, not the others.
    _load(service, FakeTournament())
    read = WebOps.get_tournament_sets()
    assert [row["id"] for row in read["rows"]] == [1]
    assert read["query"] == {"phase": 12, "finished": True}
    assert read["status"] == "idle"
    assert WebOps.get_tournament_phases()["rows"] == []
    assert WebOps.get_tournament_stations()["rows"] == []

    # One list, as the window has one cache: another tournament's fetch
    # replaces it.
    other = FakeTournament(url=OTHER_URL, phases=[])
    _load(service, other)
    assert service.RefreshSets(None, False) == STARTED
    _drain(autonaApp)
    _load(service, FakeTournament())
    assert WebOps.get_tournament_sets()["rows"] == []


def test_the_adapter_forgets_on_every_load_and_unload(service, autonaApp):
    """Called directly by LoadTournament / UnloadTournament, once the new
    provider (or none) is in place, as AnalysisService's (S4j review M2): a
    fetch asked for before is dropped, and one asked for after is the new
    tournament's. Nothing is fetched: the adapter's jobs stay queued."""
    from src.TournamentDataProvider.StartGGDataProvider import (
        StartGGDataProvider)
    from src.core.TaskExecutor import TaskExecutor
    from tests.test_provider_jobs import URL as LOADED

    adapter = AutonaTournamentDataProvider.instance
    stepped = TaskExecutor(stepped=True)
    forgotten = []
    try:
        # Its own: undoing the test's `monkeypatch` takes the fixtures' with it.
        with pytest.MonkeyPatch.context() as patching:
            patching.setattr(TaskExecutor, "instance", stepped)
            patching.setattr(StartGGDataProvider, "GetEntrants",
                             lambda self, cancel_event=None: None)
            patching.setattr(service, "Forget", lambda: forgotten.append(
                getattr(adapter.provider, "url", None)))
            assert adapter.LoadTournament(LOADED) is True
            assert adapter.LoadTournament(LOADED) is False  # the same: nothing
            assert adapter.LoadTournament(LOADED, force=True) is True
            adapter.UnloadTournament()
            assert forgotten == [LOADED, LOADED, None]
    finally:
        stepped.shutdown(5)
        autonaApp.settle()


# ── the match bar's pool names (ADR-005 S4x review) ──────────────────────
# The phases a fetch answers are MatchNameCandidates' too: v1's
# phases:refresh reached the set list alone, the adapter's own fetch
# (tournament_phases_updated) being the one source of the pool names.

POOL_PHASES = [{"id": 21, "name": "Round 1", "groups": [
    {"id": 201, "name": "R1 Pool 7", "bracketType": "DOUBLE_ELIMINATION"},
    {"id": 202, "name": "R1 Pool 8", "bracketType": "DOUBLE_ELIMINATION"}]}]
POOL_NAMES = ["R1 Pool 7", "R1 Pool 8"]


def _pools():
    from src.core.MatchNameCandidates import MatchNameCandidates
    return MatchNameCandidates.instance.Candidates(1)["pool"]


def test_the_phases_fetched_are_the_match_bar_s_pool_names(
        client, service, autonaApp, monkeypatch):
    from src.core.MatchNameCandidates import MatchNameCandidates
    candidates = MatchNameCandidates.instance
    follow = candidates.FollowPhases
    held = []

    def spy(phases):
        # With the end published, inside StateManager.lock (a tournament
        # change cannot come between) and outside the service's.
        held.append((StateManager.lock._is_owned(), service._lock._is_owned()))
        follow(phases)

    monkeypatch.setattr(candidates, "FollowPhases", spy)
    terms = _pools()
    provider = FakeTournament(phases=POOL_PHASES)
    _load(service, provider)
    _post(client, "/api/v1/tournament/phases:refresh")
    _drain(autonaApp)
    assert _pools() == POOL_NAMES + terms
    response = client.get("/api/v1/match-names/candidates?board=1")
    assert response.status_code == 200
    assert response.get_json()["data"]["pool"] == POOL_NAMES + terms
    assert held == [(True, False)]

    # An event with no phases has no pools: the names go.
    provider.answers[PHASES] = [[]]
    _fetched(service, autonaApp, provider)
    assert _pools() == terms
    assert held == [(True, False)] * 2


def test_a_failed_or_dropped_phase_fetch_names_no_pool(service, autonaApp):
    from src.core.MatchNameCandidates import MatchNameCandidates
    terms = _pools()
    provider = FakeTournament(phases=POOL_PHASES)
    _load(service, provider)
    _fetched(service, autonaApp, provider)

    # Failed: the names of the last fetch that did not fail stay.
    provider.failing.add(PHASES)
    assert service.RefreshPhases() == STARTED
    _drain(autonaApp)
    assert _progress(PHASES)["status"] == "error"
    assert _pools() == POOL_NAMES + terms

    # Dropped by a tournament change: its late answer names nothing. (The
    # adapter's unload emptied the names, as it does after Forget.)
    MatchNameCandidates.instance.FollowPhases([])
    provider.failing.clear()
    release = threading.Event()
    provider.gates[PHASES] = [None, None, release]
    assert service.RefreshPhases() == STARTED
    provider.waitAsked(PHASES, 3)
    _load(service, FakeTournament(url=OTHER_URL, phases=[]))
    release.set()
    _drain(autonaApp)
    assert _pools() == terms


def test_a_tournament_change_leaves_the_names_to_the_adapter(
        service, autonaApp):
    """Forget does not empty them: the adapter's own payloads replace them
    right after it -- the empty ones of an unload, the new tournament's
    phases of a load (which the adapter fetches non-strictly: a failure is
    an empty list) -- and the desktop's combos read them again on exactly
    those, so both stay in step."""
    terms = _pools()
    provider = FakeTournament(phases=POOL_PHASES)
    _load(service, provider)
    _fetched(service, autonaApp, provider)
    service.Forget()
    assert _pools() == POOL_NAMES + terms

    # What UnloadTournament emits after its Forget.
    AutonaTournamentDataProvider.instance.signals \
        .tournament_phases_updated.emit([])
    assert _pools() == terms


def test_no_tournament_starts_nothing_and_publishes_nothing(
        service, autonaApp):
    _load(service, None)
    published = {kind: _progress(kind) for kind in (PHASES, SETS, STATIONS)}
    assert service.RefreshPhases() == NO_TOURNAMENT
    assert service.RefreshStations() == NO_TOURNAMENT
    assert service.RefreshSets(None, False) == NO_TOURNAMENT
    assert {kind: _progress(kind)
            for kind in (PHASES, SETS, STATIONS)} == published
    assert not service.Running(SETS)
    assert service._alive == {}


def test_a_replacement_with_no_tournament_takes_back_what_it_stopped(
        service, autonaApp):
    """The tournament went (the moment before its Forget): the fetch it
    replaced was told to stop, and nothing is left running or saying so."""
    provider = FakeTournament(phases=[])
    release = threading.Event()
    provider.gates[SETS] = [release]
    _load(service, provider)
    assert service.RefreshSets(None, False) == STARTED
    provider.waitAsked(SETS)
    revision = _progress(SETS)["revision"]

    AutonaTournamentDataProvider.instance.provider = None
    assert service.RefreshSets(None, True) == NO_TOURNAMENT
    assert provider.cancels[0].is_set()
    assert not service.Running(SETS)
    assert _progress(SETS) == schema.DefaultSetSelectionFetch(revision + 1)

    release.set()
    _drain(autonaApp)
    assert service.Result(SETS)["rows"] == []
    assert _progress(SETS)["status"] == "idle"


def test_an_answer_while_the_app_quits_is_dropped(service, autonaApp):
    """What a request the shutdown cut short answered is no answer: here,
    nothing where the last fetch had rows. They stay, and nothing is published."""
    provider = FakeTournament()
    provider.answers[PHASES] = [PHASE_ANSWER, []]
    _load(service, provider)
    _fetched(service, autonaApp, provider)
    kept = service.Result(PHASES)
    assert kept["rows"]

    release = threading.Event()
    provider.gates[PHASES] = [None, release]
    assert service.RefreshPhases() == STARTED
    provider.waitAsked(PHASES, 2)
    published = _progress(PHASES)
    assert published["status"] == "running"

    with pytest.MonkeyPatch.context() as patching:
        patching.setattr(selection, "_Stopping", lambda: True)
        release.set()
        _until(autonaApp, lambda: not service._alive[PHASES], "the job over")

    assert service.Result(PHASES) == kept
    assert _progress(PHASES) == published


def test_an_end_that_could_not_be_published_is_published_again(
        service, autonaApp, monkeypatch):
    """Or the list would say running for good, and the next fetch find it
    over."""
    provider = FakeTournament()
    _load(service, provider)
    real = SetSelectionService._Set
    failures = []

    def setOnce(kind, value):
        if value["status"] == "done" and not failures:
            failures.append(value)
            raise RuntimeError("the state could not be written")
        real(kind, value)

    monkeypatch.setattr(SetSelectionService, "_Set", staticmethod(setOnce))
    assert service.RefreshPhases() == STARTED
    _drain(autonaApp)
    assert failures
    assert _progress(PHASES)["status"] == "done"
    assert not service.Running(PHASES)
    assert service.Result(PHASES)["rows"]


def test_no_lock_is_held_around_the_fetcher_or_the_provider(
        service, autonaApp):
    """Agent-pitfalls #17: nothing here waits inside StateManager.lock or its
    own lock (the fixture's _OwnedLock fails a second take by its holder)."""
    provider = FakeTournament()
    _load(service, provider)
    _fetched(service, autonaApp, provider)
    _fetched(service, autonaApp, provider, STATIONS)
    assert service.RefreshSets(ALL_PHASES, False) == STARTED
    _drain(autonaApp)
    assert service.fetcherLocks == [(False, False)] * 3
    assert provider.locksHeld == [False] * 3


# ── the rows ─────────────────────────────────────────────────────────────

def test_a_row_is_only_what_start_gg_shows_anyone():
    """Whitelisted, so that a READ can return it. A real name, a handle or the
    characters of a game the parse put in the set stay out -- so nothing of a
    row goes stale when the game changes, and a game change drops nothing."""
    row = selection.SetRow(_set(1001, ("ALPHA", "ALICE"), ("BRAVO",),
                                stream="channel", station=None))
    assert row == {
        "id": 1001, "stream": "channel", "station": "", "phase": "Pools",
        "pool": "Pool A", "round": "Winners Final",
        "p1": {"name": "ALPHA", "members": ["ALPHA", "ALICE"]},
        "p2": {"name": "BRAVO", "members": ["BRAVO"]},
    }
    text = json.dumps(row)
    assert "real name" not in text
    assert "handle_of" not in text
    assert "mario" not in text


def test_a_set_with_nobody_yet_has_empty_sides():
    row = selection.SetRow(_set(1, (), ()))
    assert row["p1"] == {"name": "", "members": []}
    assert row["p2"] == {"name": "", "members": []}
    bare = {"id": 2, "round_name": ""}
    assert selection.SetRow(bare) == {
        "id": 2, "stream": "", "station": "", "phase": "", "pool": "",
        "round": "", "p1": {"name": "", "members": []},
        "p2": {"name": "", "members": []}}


@pytest.mark.parametrize("unread", [{}, None, "x", {"id": 1}])
def test_what_the_parse_could_not_read_is_no_row(unread):
    assert selection.SetRow(unread) is None


def test_the_phase_and_station_rows_are_whitelisted_too():
    assert selection.PhaseRows([{"id": 1, "name": None, "extra": 1, "groups": [
        {"id": 2, "name": "Pool", "bracketType": "X", "extra": 1}]}]) == [
        {"id": 1, "name": "", "groups": [
            {"id": 2, "name": "Pool", "bracketType": "X"}]}]
    assert selection.PhaseRows(None) == []
    assert selection.StationRows([
        {"id": 90, "identifier": "channel", "type": "stream", "extra": 1},
        {"id": 77, "identifier": 4, "type": "station", "stream": None}]) == [
        {"type": "stream", "id": 90, "identifier": "channel", "stream": ""},
        {"type": "station", "id": 77, "identifier": 4, "stream": ""}]
    assert selection.StationRows(None) == []


def test_the_state_says_idle_at_every_launch_at_the_revision_it_had(
        service, autonaApp):
    """A fetch and its rows do not outlive the process, and the next one goes
    on from the previous session's revision. Seeded with the rest."""
    StateManager.Set(f"{schema.SET_SELECTION_ROOT}.sets", dict(
        schema.DefaultSetSelectionFetch(7), status="running", done=1))
    schema.SeedPanelState()
    assert _progress(SETS) == schema.DefaultSetSelectionFetch(7)
    assert set(StateManager.Get(schema.SET_SELECTION_ROOT)) == set(
        schema.SET_SELECTION_KINDS)


@pytest.mark.parametrize("value", [None, "x", {"status": 1, "revision": True},
                                   {"done": -1, "error": 3}])
def test_a_malformed_progress_reads_as_idle(service, value):
    StateManager.Set(f"{schema.SET_SELECTION_ROOT}.sets", value)
    assert _progress(SETS) == schema.DefaultSetSelectionFetch()


# ── the routes ───────────────────────────────────────────────────────────

@pytest.fixture
def client(service, webApp):  # noqa: F811
    return webApp.test_client()


def _get(client, path):
    response = client.get(path)
    assert response.status_code == 200, response.get_data(as_text=True)
    body = response.get_json()
    assert body["ok"] is True and isinstance(body["revision"], int)
    return body["data"]


def _post(client, path, body=None, status=202, code=None):
    response = client.post(path, json=body or {})
    assert response.status_code == status, response.get_data(as_text=True)
    payload = response.get_json()
    if code is not None:
        assert payload["ok"] is False
        assert payload["error"]["code"] == code
    return payload


ROUTES = [("/api/v1/tournament/phases", PHASES),
          ("/api/v1/tournament/stations", STATIONS),
          ("/api/v1/tournament/sets", SETS)]


def test_reading_never_fetches(client, service, autonaApp):
    """As opening a window never does. An idle list is one to ask for."""
    provider = FakeTournament()
    _load(service, provider)
    for path, kind in ROUTES:
        data = _get(client, path)
        assert data["status"] == "idle" and data["rows"] == []
        assert data["url"] == "" and data["fetchedAt"] is None
    _drain(autonaApp)
    assert provider.asked == {PHASES: [], SETS: [], STATIONS: []}


@pytest.mark.parametrize("path, kind", ROUTES)
def test_a_list_is_fetched_and_read(client, service, autonaApp, path, kind):
    _load(service, FakeTournament())
    if kind == SETS:
        _post(client, "/api/v1/tournament/phases:refresh")
        _drain(autonaApp)
    before = _progress(kind)["revision"]
    body = {"phase": "all"} if kind == SETS else None

    answer = _post(client, f"{path}:refresh", body)
    assert answer["data"] == {
        "accepted": True,
        "operation": f"tournament.{kind}.refresh"}
    assert _progress(kind)["revision"] == before + 1
    _drain(autonaApp)

    data = _get(client, path)
    assert data["status"] == "done"
    assert data["url"] == URL
    assert isinstance(data["fetchedAt"], int)
    assert data["rows"]
    expected = {"status", "url", "fetchedAt", "rows"}
    assert set(data) == (expected | {"query"} if kind == SETS else expected)


def test_the_shapes_of_the_rows(client, service, autonaApp):
    _load(service, FakeTournament())
    for path, _kind in ROUTES[:2]:
        _post(client, f"{path}:refresh")
    _drain(autonaApp)
    _post(client, "/api/v1/tournament/sets:refresh",
          {"phase": 11, "finished": True})
    _drain(autonaApp)

    assert _get(client, "/api/v1/tournament/phases")["rows"][0] == {
        "id": 11, "name": "Pools", "groups": [
            {"id": 101, "name": "Pool A",
             "bracketType": "DOUBLE_ELIMINATION"},
            {"id": 102, "name": "Pool B",
             "bracketType": "DOUBLE_ELIMINATION"}]}
    assert _get(client, "/api/v1/tournament/stations")["rows"] == [
        {"type": "station", "id": 77, "identifier": 4, "stream": "channel"},
        {"type": "station", "id": 78, "identifier": 5, "stream": ""},
        {"type": "stream", "id": 90, "identifier": "channel", "stream": ""}]
    sets = _get(client, "/api/v1/tournament/sets")
    assert sets["query"] == {"phase": 11, "finished": True}
    assert sets["rows"] == [{
        "id": 1, "stream": "", "station": "3", "phase": "Pools",
        "pool": "Pool A", "round": "Winners Final",
        "p1": {"name": "ALPHA", "members": ["ALPHA"]},
        "p2": {"name": "BRAVO", "members": ["BRAVO"]}}]
    assert "real name" not in json.dumps(sets)


@pytest.mark.parametrize("path, kind", ROUTES)
def test_no_tournament_is_not_ready(client, service, path, kind):
    _load(service, None)
    published = _progress(kind)
    _post(client, f"{path}:refresh", status=409, code="NOT_READY")
    assert _progress(kind) == published


@pytest.mark.parametrize("path, kind", ROUTES[:2])
def test_one_at_a_time_is_a_conflict(client, service, autonaApp, path, kind):
    provider = FakeTournament()
    release = threading.Event()
    provider.gates[kind] = [release]
    _load(service, provider)
    _post(client, f"{path}:refresh")
    provider.waitAsked(kind)
    _post(client, f"{path}:refresh", status=409, code="CONFLICT")
    release.set()
    _drain(autonaApp)
    assert len(provider.asked[kind]) == 1


def test_the_set_list_route_answers_by_query(client, service, autonaApp):
    provider = FakeTournament(phases=[])
    release = threading.Event()
    provider.gates[SETS] = [release]
    _load(service, provider)
    _post(client, "/api/v1/tournament/sets:refresh")
    provider.waitAsked(SETS)
    _post(client, "/api/v1/tournament/sets:refresh", status=409,
          code="CONFLICT")
    _post(client, "/api/v1/tournament/sets:refresh", {"finished": True})
    provider.waitAsked(SETS, 2)
    assert provider.cancels[0].is_set()
    release.set()
    _drain(autonaApp)
    assert _get(client, "/api/v1/tournament/sets")["query"] == {
        "phase": ALL_PHASES, "finished": True}


@pytest.mark.parametrize("body, status, code", [
    ({"phase": True}, 400, "INVALID_ARGUMENT"),
    ({"phase": False}, 400, "INVALID_ARGUMENT"),
    ({"phase": 1.5}, 400, "INVALID_ARGUMENT"),
    ({"phase": ""}, 400, "INVALID_ARGUMENT"),
    ({"phase": [11]}, 400, "INVALID_ARGUMENT"),
    ({"phase": 0}, 400, "INVALID_ARGUMENT"),
    ({"phase": -1}, 400, "INVALID_ARGUMENT"),
    ({"phase": 11, "finished": "yes"}, 400, "INVALID_ARGUMENT"),
    ({"phase": 11, "finished": 1}, 400, "INVALID_ARGUMENT"),
    ({"phase": 11, "finished": None}, 400, "INVALID_ARGUMENT"),
    # A phase must be chosen: "all" is there for every one.
    ({}, 400, "INVALID_ARGUMENT"),
    ({"phase": None}, 400, "INVALID_ARGUMENT"),
    ({"phase": 999}, 404, "NOT_FOUND"),
    ({"phase": "999"}, 404, "NOT_FOUND"),
])
def test_what_the_set_list_route_refuses(
        client, service, autonaApp, body, status, code):
    provider = FakeTournament()
    _load(service, provider)
    _post(client, "/api/v1/tournament/phases:refresh")
    _drain(autonaApp)
    published = _progress(SETS)
    _post(client, "/api/v1/tournament/sets:refresh", body, status=status,
          code=code)
    assert _progress(SETS) == published
    assert provider.asked[SETS] == []


def test_the_cancel_route(client, service, autonaApp):
    provider = FakeTournament(phases=[])
    release = threading.Event()
    provider.gates[SETS] = [release]
    _load(service, provider)
    assert _post(client, "/api/v1/tournament/sets:cancel",
                 status=200)["data"] == {"cancelled": False}
    _post(client, "/api/v1/tournament/sets:refresh")
    provider.waitAsked(SETS)
    assert _post(client, "/api/v1/tournament/sets:cancel",
                 status=200)["data"] == {"cancelled": True}
    assert provider.cancels[0].is_set()
    release.set()
    _drain(autonaApp)
    assert _get(client, "/api/v1/tournament/sets")["status"] == "idle"


def test_the_rows_and_the_status_are_read_in_one_hold(
        client, service, autonaApp, monkeypatch):
    """So that a reader is never handed a fetch's rows with the status before
    it ended, or idle with the rows of the tournament just left."""
    _load(service, FakeTournament())
    _fetched(service, autonaApp, None)
    adapter = AutonaTournamentDataProvider.instance
    held = []

    def spy(name, real):
        def call(*args, **kwargs):
            held.append((name, StateManager.lock._is_owned()))
            return real(*args, **kwargs)
        return call

    monkeypatch.setattr(service, "Result", spy("Result", service.Result))
    monkeypatch.setattr(SetSelectionService, "Progress", staticmethod(
        spy("Progress", SetSelectionService.Progress)))
    monkeypatch.setattr(adapter, "GetProvider",
                        spy("GetProvider", adapter.GetProvider))
    assert WebOps.get_tournament_phases()["status"] == "done"
    assert sorted(held) == [("GetProvider", True), ("Progress", True),
                            ("Result", True)]


# ── access (ADR-005 S3a): this PC without the token, others with it ─────

def test_the_routes_are_classified():
    for endpoint in ("get_tournament_phases", "get_tournament_sets",
                     "get_tournament_stations", "get_scoreboard_follow"):
        assert WebAccess.ROUTE_ACCESS[f"api_v1.{endpoint}"] == WebAccess.READ
    for endpoint in ("tournament_phases_refresh", "tournament_sets_refresh",
                     "tournament_sets_cancel", "tournament_stations_refresh",
                     "follow", "stop_follow"):
        assert WebAccess.ROUTE_ACCESS[f"api_v1.{endpoint}"] == WebAccess.WRITE


@pytest.fixture
def peer(monkeypatch, webApp):  # noqa: F811
    from src.AutonaWebServer import WebServer
    from tests.test_web_access import LAN_HOST, LAN_HOST_2, LAN_PEER

    monkeypatch.setattr(SettingsManager, "settings", {})
    monkeypatch.setattr(SettingsManager, "SaveSettings", lambda: None)
    monkeypatch.setattr(SettingsManager, "loadFailed", False)
    monkeypatch.setattr(WebAccess, "_token", None)
    monkeypatch.setattr(WebServer, "_lan_hosts", (LAN_HOST, LAN_HOST_2))
    other = webApp.test_client()
    other.environ_base["REMOTE_ADDR"] = LAN_PEER
    return other


@pytest.mark.parametrize("path", [
    "/api/v1/tournament/phases:refresh", "/api/v1/tournament/sets:refresh",
    "/api/v1/tournament/sets:cancel", "/api/v1/tournament/stations:refresh",
    "/api/v1/scoreboards/1:follow", "/api/v1/scoreboards/1:stop-follow"])
def test_a_fetch_or_a_follow_needs_the_token_from_another_computer(
        autonaApp, service, peer, path):
    refused = peer.post(path, json={})
    assert refused.status_code == 401
    assert refused.get_json()["error"]["code"] == "UNAUTHENTICATED"


@pytest.mark.parametrize("path", [
    "/api/v1/tournament/phases", "/api/v1/tournament/sets",
    "/api/v1/tournament/stations", "/api/v1/scoreboards/1/follow"])
def test_the_reads_need_no_token(autonaApp, service, peer, path):
    assert peer.get(path).status_code == 200


# ── both entrances ask the same and show the same ────────────────────────

@pytest.fixture
def setWindow(service, autonaApp):
    from qtpy.QtWidgets import QWidget
    from src.AutonaSelectSetWindow import AutonaSelectSetWindow

    AutonaSelectSetWindow._cache = None
    AutonaSelectSetWindow._sortState = None
    parent = QWidget()
    window = AutonaSelectSetWindow(parent)
    try:
        yield window
    finally:
        adapter = AutonaTournamentDataProvider.instance
        adapter.CancelSetLoad()
        window.deleteLater()
        parent.deleteLater()
        AutonaSelectSetWindow._cache = None
        AutonaSelectSetWindow._sortState = None
        autonaApp.settle()


@pytest.mark.parametrize("comboIndex, phase, finished", [
    (1, ALL_PHASES, False),      # All phases
    (1, ALL_PHASES, True),
    (2, 11, False),              # Pools
    (3, 12, True),               # Top 8
])
def test_the_window_and_v1_ask_start_gg_the_same(
        service, autonaApp, setWindow, comboIndex, phase, finished):
    provider = FakeTournament()
    _load(service, provider)
    setWindow._onPhasesUpdated(PHASE_ANSWER)
    setWindow.phaseCombo.setCurrentIndex(comboIndex)
    setWindow.showFinished.setChecked(finished)
    setWindow._FetchSets()
    _drain(autonaApp)

    _fetched(service, autonaApp, provider)
    assert service.RefreshSets(phase, finished) == STARTED
    _drain(autonaApp)

    assert len(provider.asked[SETS]) == 2
    assert provider.asked[SETS][0] == provider.asked[SETS][1]


def test_the_window_and_v1_ask_the_same_with_no_phases(
        service, autonaApp, setWindow):
    provider = FakeTournament(phases=[])
    _load(service, provider)
    setWindow._onPhasesUpdated([])
    setWindow._FetchSets()
    _drain(autonaApp)
    assert service.RefreshSets(None, False) == STARTED
    _drain(autonaApp)
    assert provider.asked[SETS] == [{"finished": False, "groups": None}] * 2


def test_the_window_and_v1_refuse_the_same_with_none_chosen(
        service, autonaApp, setWindow):
    provider = FakeTournament()
    _load(service, provider)
    setWindow._onPhasesUpdated(PHASE_ANSWER)
    setWindow.phaseCombo.setCurrentIndex(0)      # Select a phase...
    setWindow._FetchSets()
    _drain(autonaApp)
    _fetched(service, autonaApp, provider)
    assert service.RefreshSets(None, False) == PHASE_REQUIRED
    assert provider.asked[SETS] == []


def _side(row, side):
    """What the page writes in a player column: the window's rule for doubles,
    which looks at the first side only (external-control-api.md §5.18)."""
    from qtpy.QtWidgets import QApplication

    name = row[side]["name"]
    if len(row["p1"]["members"]) > 1:
        name += (" " + QApplication.translate("punctuation", "(")
                 + ", ".join(row[side]["members"])
                 + QApplication.translate("punctuation", ")"))
    return name


def _columns(row):
    return [row["stream"], row["station"], row["phase"], row["pool"],
            row["round"], _side(row, "p1"), _side(row, "p2")]


def _complete(row):
    return row["p1"]["name"] != "" and row["p2"]["name"] != ""


def _table(window):
    model = window.model
    return [[model.item(r, c).text() for c in range(7)]
            for r in range(model.rowCount())]


SHOWN = [
    _set(1),
    _set(2, stream="channel", station=None, tournament_pool=""),
    _set(3, ("ALPHA", "ALICE"), ("BRAVO", "BOB"), round_name="Grand Final"),
    # A doubles set with one side still unknown, and the other way around.
    _set(4, ("ALPHA", "ALICE"), ()),
    _set(5, (), ("BRAVO", "BOB")),
    _set(6, (), ()),
]


def test_the_window_shows_what_the_rows_say(service, autonaApp, setWindow):
    """The columns from v1's rows, by the rules the page is told, are the
    window's table: and its "Show complete pairs" is the rows' filter."""
    provider = FakeTournament(phases=[], sets=SHOWN)
    _load(service, provider)
    setWindow._onPhasesUpdated([])
    setWindow._FetchSets()
    _drain(autonaApp)
    table = _table(setWindow)
    assert len(table) == len(SHOWN)

    assert service.RefreshSets(None, False) == STARTED
    _drain(autonaApp)
    rows = service.Result(SETS)["rows"]
    assert [_columns(row) for row in rows] == table
    # The hidden column the search reaches and the load reads.
    assert [str(row["id"]) for row in rows] == [
        setWindow.model.item(r, setWindow.DATA_COLUMN).text()
        for r in range(setWindow.model.rowCount())]

    setWindow.showCompletePairs.setChecked(True)
    setWindow._RenderSets(setWindow._CachedSets())
    assert [_columns(row) for row in rows if _complete(row)] == _table(
        setWindow)


def test_the_station_window_shows_what_the_rows_say(service, autonaApp):
    from qtpy.QtWidgets import QWidget
    from src.AutonaSelectStationWindow import AutonaSelectStationWindow

    provider = FakeTournament()
    _load(service, provider)
    parent = QWidget()
    window = AutonaSelectStationWindow(parent)
    try:
        window.LoadStations()
        _drain(autonaApp)
        model = window.proxyModel.sourceModel()
        table = [[model.item(r, c).text() for c in range(4)]
                 for r in range(model.rowCount())]
        assert len(table) == len(STATION_ANSWER)

        _fetched(service, autonaApp, provider, STATIONS)
        assert [[row["type"], str(row["id"]), row["stream"],
                 str(row["identifier"])]
                for row in service.Result(STATIONS)["rows"]] == table
    finally:
        window.deleteLater()
        parent.deleteLater()
        autonaApp.settle()
