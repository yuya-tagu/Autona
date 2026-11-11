"""The lists a set is chosen from: the tournament's phases, its sets, its
stations and streams (ADR-005 S4v).

What the Qt set selection and station windows showed (AutonaSelectSetWindow,
AutonaSelectStationWindow), for a shell with no window and for the SCORE page:
the windows' fetches lived in the provider's adapter, answered through its Qt
signals and could not tell a failed request from an empty answer. Which set is
loaded, and which station followed, is SetFollowService's whichever list it was
chosen from: this only fetches and keeps the lists. The windows went with the
Qt SCORE (ADR-005 S4ac-3); the adapter's fetches they used are still there.

Each list is fetched when asked, never by itself (opening the window never
hits the network; the page reads, and asks when there is nothing to read):

- ``RefreshPhases()`` / ``RefreshStations()``: ``STARTED``, ``BUSY`` while
  that list's fetch runs (the request is dropped, not queued) or
  ``NO_TOURNAMENT``;
- ``RefreshSets(phase, finished)``: ``phase`` a phase's id, ``ALL_PHASES`` or
  None. While the tournament's phases are known, None is ``PHASE_REQUIRED``
  (fetching every phase of a large event is what choosing one avoids, and "all"
  is there for it) and an id it does not have ``UNKNOWN_PHASE``; with no phases
  known the whole event is fetched, and an id is ``UNKNOWN_PHASE``. The query
  is kept in one form, so that the same request is one: a phase's id as the
  phases have it (``"11"`` is ``11``), the whole event ``ALL_PHASES`` (None
  with no phases known is that). A fetch running for the same query answers
  ``BUSY``. One for another query is stopped and replaced, as the window's
  phase combo and checkbox do while it fetches;
- any Refresh, with ``MAX_SET_JOBS`` of that list's jobs still out (a stopped
  one's request has not answered yet, whatever stopped it: a replacement, a
  cancel, a tournament change): ``BUSY``, because the job lane's four threads
  are every tournament fetch's (S3f-2 decision 7);
- ``CancelSets()``: stop the set list's fetch, as choosing a set in the window
  does. The rows stay.

How each goes is published in the state, ``set_selection.{phases,sets,stations}``
-- ``{status, error, done, total, failed, revision}``
(StateSchema.ReadSetSelectionFetch): ``running``, ``done``, ``error``
(``fetch_failed``) or ``idle`` (nothing running: never fetched, stopped, or the
tournament changed). Every fetch and every drop takes the list's next revision,
published before the call returns. ``done`` / ``total`` are the set list's pages
(0 total: not known yet, the window's busy bar); ``failed`` what is missing from
a ``done``: the pages that could not be fetched and the sets that could not be
read.

A failed request is told from an empty answer: the provider is asked strictly
(``strict=True``). ``done`` with no rows is an event with nothing in that list,
``error`` a fetch that failed, and it keeps the rows of the last one that did
not. The rows are not in the state: they stay here with the URL of their
tournament (``Result``), and what a reader is handed goes through that URL.

``Forget()``: the provider adapter calls it right after the provider is replaced
or unset, as AnalysisService's. What runs is dropped (each fetch is an object
of its own; its late answer finds itself no longer current), every list goes
back to idle. The phases and the stations are dropped: the window's are read
again for every load and every open. The set list stays, tied to its URL: the
window's cache comes back when the same tournament is loaded again.

A game change drops nothing: no row holds anything of the game (a row is
whitelisted: `SetRow`). The characters a set's parse looks up are not in it.

The phases a fetch answers are the match bar's pool names too
(MatchNameCandidates.FollowPhases, with the end published: `_FollowCandidates`,
ADR-005 S4x review). A stopped or late answer is not. ``Forget()`` leaves
the names alone: the provider adapter's own payloads replace them -- the empty
ones of an unload, the new tournament's phases of a load -- in step with the
desktop's combos, which read them again on those signals.

Lock order: ``StateManager.lock``, then this service's own, held only around
its own fields; the state is written, a fetch made and the fetcher called with
it released. Rows are never changed once made, so ``Result`` hands them over
without copying (a reader only serializes them, inside no lock of ours).

Qt-free (tests/test_core_qt_free.py). Import side effects: none
(agent-pitfalls #1). ``SetSelectionService.instance`` is built by
``bootstrap_core()``.
"""
import collections
import threading
import time

from loguru import logger

from . import StateSchema as schema
from .MatchNameCandidates import MatchNameCandidates
from .StateManager import StateManager
from .TaskExecutor import TaskExecutor

PATH = schema.SET_SELECTION_ROOT
PHASES, SETS, STATIONS = schema.SET_SELECTION_KINDS

# What the Refresh calls answer.
STARTED = "started"
BUSY = "busy"
NO_TOURNAMENT = "no tournament"
PHASE_REQUIRED = "phase required"
UNKNOWN_PHASE = "unknown phase"

# RefreshSets' phase for every phase of the event.
ALL_PHASES = "all"

# status
IDLE = "idle"
RUNNING = "running"
DONE = "done"
ERROR = "error"
# error, with ERROR ("" otherwise).
FETCH_FAILED = "fetch_failed"

# The most jobs of one list out at the same time: the one wanted, and one
# stopped whose request has not answered yet (see the module docstring).
MAX_SET_JOBS = 2


# ── the rows ─────────────────────────────────────────────────────────────

def PhaseRows(phases) -> list:
    """The provider's phases as rows: {"id", "name", "groups": [{"id", "name",
    "bracketType"}]}. Nothing else of a phase."""
    rows = []
    for phase in phases if isinstance(phases, list) else []:
        if not isinstance(phase, dict):
            continue
        rows.append({
            "id": phase.get("id"),
            "name": phase.get("name") or "",
            "groups": [{"id": group.get("id"),
                        "name": group.get("name") or "",
                        "bracketType": group.get("bracketType")}
                       for group in phase.get("groups") or []
                       if isinstance(group, dict)],
        })
    return rows


def PhaseGroupIds(phase) -> list:
    """The groups of a phase row, as the window hands them to the fetch."""
    return [group["id"] for group in phase["groups"]]


def _Side(data, entrants, index) -> dict:
    team = entrants[index] if index < len(entrants) else []
    return {"name": data.get(f"p{index + 1}_name") or "",
            "members": [(member.get("gamerTag") or "")
                        for member in team or []
                        if isinstance(member, dict)]}


def SetRow(data):
    """One parsed set as a row, or None for what could not be read (the
    provider's parse answers {}). Only these fields: a tag and an entrant's
    name are what start.gg shows anyone, and whatever else the parse put in the
    set -- the characters, a real name -- stays out, so that this can be read
    without the player database's access.

    Text as the window's columns show it; the wording around it -- " (A, B)"
    for doubles -- is the reader's. `members`: a side's tags, in order."""
    if not isinstance(data, dict) or "round_name" not in data:
        return None
    station = data.get("station")
    entrants = data.get("entrants") or []
    return {
        "id": data.get("id"),
        "stream": data.get("stream") or "",
        "station": "" if station is None else str(station),
        "phase": data.get("tournament_phase") or "",
        "pool": data.get("tournament_pool") or "",
        "round": data.get("round_name") or "",
        "p1": _Side(data, entrants, 0),
        "p2": _Side(data, entrants, 1),
    }


def StationRows(stations) -> list:
    """The provider's stations and streams as rows: {"type", "id",
    "identifier", "stream"} ("" when it names no stream)."""
    return [{"type": station.get("type") or "",
             "id": station.get("id"),
             "identifier": station.get("identifier"),
             "stream": station.get("stream") or ""}
            for station in (stations if isinstance(stations, list) else [])
            if isinstance(station, dict)]


# ── the fetch ────────────────────────────────────────────────────────────

class _Fetch:
    """One Refresh. Its fields are the service's lock's."""

    def __init__(self, kind, query):
        self.kind = kind
        self.query = query
        # What tells the provider to stop asking (the set list's pages).
        self.cancel = threading.Event()
        # Set when the Refresh publishes it (None until then: nothing of it
        # is published before).
        self.revision = None
        self.url = ""
        self.done = 0
        self.total = 0
        self.failed = 0
        self.status = RUNNING
        self.error = ""
        self.rows = None
        self.fetchedAt = None
        self.over = False

    def Snapshot(self):
        return {"status": self.status, "error": self.error,
                "done": self.done, "total": self.total,
                "failed": self.failed, "revision": self.revision}


class SetSelectionService:
    """The set selection's lists (see the module docstring)."""

    instance: "SetSelectionService" = None

    def __init__(self, fetcher=None):
        """`fetcher(job, status)` runs `job(provider)` on a job of the task
        executor with the tournament loaded now and returns True, or returns
        False, running nothing, when there is none (the provider adapter's
        RunWithProvider)."""
        self._fetcher = fetcher
        self._lock = threading.Lock()
        # kind -> the newest fetch (over or not). None / absent: none.
        self._fetches = {}
        # kind -> the newest fetch that ended with rows, once published.
        self._results = {}
        # kind -> its jobs submitted and not over (a stopped fetch's too:
        # their requests are still out).
        self._alive = collections.Counter()

    # ── reading ──────────────────────────────────────────────────────────

    @staticmethod
    def Progress(kind) -> dict:
        """List `kind`'s progress as published. Any thread."""
        return schema.ReadSetSelectionFetch(kind)

    def Running(self, kind) -> bool:
        """Whether a fetch of list `kind` is in progress."""
        with self._lock:
            fetch = self._fetches.get(kind)
            return fetch is not None and not fetch.over

    def Result(self, kind) -> dict:
        """The rows of list `kind`'s newest fetch that made them: {"url": the
        tournament's, "rows", "query": what it was asked for, "fetchedAt": unix
        seconds, "revision": that fetch's}. No rows ({"url": "", "rows": [],
        ...None}) before the first one. The rows are never changed once made
        and are handed as they are: read them, do not change them. Any
        thread."""
        with self._lock:
            fetch = self._results.get(kind)
            if fetch is None:
                return {"url": "", "rows": [], "query": None,
                        "fetchedAt": None, "revision": None}
            return {"url": fetch.url, "rows": fetch.rows, "query": fetch.query,
                    "fetchedAt": fetch.fetchedAt, "revision": fetch.revision}

    # ── fetching ─────────────────────────────────────────────────────────

    def RefreshPhases(self) -> str:
        """Fetch the tournament's phases (see the module docstring). Any thread
        but a job's, as every Refresh."""
        return self._Start(PHASES, lambda: (None, _FetchPhases))

    def RefreshStations(self) -> str:
        """Fetch the tournament's stations and streams."""
        return self._Start(STATIONS, lambda: (None, _FetchStations))

    def RefreshSets(self, phase=None, finished=False) -> str:
        """Fetch the set list of `phase`, with the finished sets when
        `finished` (see the module docstring)."""
        def plan():
            # In _Start's lock, with making the fetch current: a tournament
            # change comes before this, or drops the fetch. Read apart, a
            # change between the two asked the next tournament for the last
            # one's groups, and published its empty answer as `done`.
            known = self._results.get(PHASES)
            phases = known.rows if known is not None else []
            chosen, groupIds = ALL_PHASES, None
            if phases:
                if phase is None:
                    return PHASE_REQUIRED
                if phase == ALL_PHASES:
                    groupIds = [groupId for row in phases
                                for groupId in PhaseGroupIds(row)]
                else:
                    row = next((row for row in phases
                                if str(row["id"]) == str(phase)), None)
                    if row is None:
                        return UNKNOWN_PHASE
                    chosen, groupIds = row["id"], PhaseGroupIds(row)
            elif phase not in (None, ALL_PHASES):
                return UNKNOWN_PHASE

            def fetchRows(fetch, provider):
                return _FetchSets(self, fetch, provider, groupIds, finished)

            return {"phase": chosen, "finished": finished}, fetchRows

        return self._Start(SETS, plan)

    def CancelSets(self) -> bool:
        """Stop the set list's fetch in progress, and drop what it still
        answers. The list goes back to idle at the next revision, its rows
        stay. False, changing nothing, when none runs."""
        with StateManager.lock:
            with self._lock:
                fetch = self._fetches.get(SETS)
                if fetch is None or fetch.over:
                    return False
                del self._fetches[SETS]
            fetch.cancel.set()
            self._SetIdle(SETS)
        return True

    def Forget(self):
        """The tournament changed: see the module docstring. Main thread: the
        provider adapter's LoadTournament / UnloadTournament, once the new
        provider (or none) is in place -- a Refresh made before this is dropped
        by it, and one made after it fetches from the new tournament."""
        with StateManager.lock:
            value = schema.IdleSetSelection(advance=1)
            with self._lock:
                running = [fetch for fetch in self._fetches.values()
                           if not fetch.over]
                self._fetches.clear()
                self._results.pop(PHASES, None)
                self._results.pop(STATIONS, None)
            for fetch in running:
                fetch.cancel.set()
            StateManager.SetIfChanged(PATH, value)

    # ── the published progress ───────────────────────────────────────────

    @staticmethod
    def _Set(kind, value):
        # One write of the whole dict: a reader never sees half of it.
        StateManager.SetIfChanged(f"{PATH}.{kind}", value)

    def _SetIdle(self, kind):
        self._Set(kind, schema.DefaultSetSelectionFetch(
            schema.ReadSetSelectionFetch(kind)["revision"] + 1))

    def _Current(self, fetch):
        # Under self._lock.
        return self._fetches.get(fetch.kind) is fetch

    def _Announce(self, fetch):
        """Publish a started fetch at the next revision -- what its job did
        meanwhile included (it publishes nothing before this)."""
        with StateManager.lock:
            revision = schema.ReadSetSelectionFetch(fetch.kind)["revision"] + 1
            with self._lock:
                if not self._Current(fetch):
                    return
                fetch.revision = revision
            self._Publish(fetch)

    def _Publish(self, fetch):
        """Publish `fetch`'s progress as it is now, if it is the current fetch
        and has been announced. Its rows become Result()'s with the end that
        published them: a reader holding StateManager.lock sees the rows and
        the status of the same moment."""
        with StateManager.lock:
            with self._lock:
                if not self._Current(fetch) or fetch.revision is None:
                    return
                value = fetch.Snapshot()
                ended = fetch.over and fetch.status == DONE
                if ended:
                    self._results[fetch.kind] = fetch
            self._Set(fetch.kind, value)
            if ended and fetch.kind == PHASES:
                _FollowCandidates(fetch.rows)

    def _Progress(self, fetch, done, total):
        """The pages of a fetch so far. A count of no pages is not known yet:
        the first page of an event with no sets."""
        with self._lock:
            if not self._Current(fetch) or fetch.over or total <= 0:
                return
            fetch.done, fetch.total = done, total
        self._Publish(fetch)

    # ── the jobs ─────────────────────────────────────────────────────────

    def _Start(self, kind, plan) -> str:
        """`plan()`, under the service's lock, answers the fetch's (query,
        fetchRows), or what to answer instead of fetching."""
        with self._lock:
            planned = plan()
            if isinstance(planned, str):
                return planned
            query, fetchRows = planned
            running = self._fetches.get(kind)
            if running is not None and running.over:
                running = None
            # Only the set list has another query to replace a fetch with. A
            # job counts whether a fetch is current or not: one that a cancel
            # or a tournament change stopped is still out.
            if (running is not None and (kind != SETS
                                         or running.query == query)) \
                    or self._alive[kind] >= MAX_SET_JOBS:
                logger.info(f"Set selection: {kind} are being fetched "
                            "already")
                return BUSY
            fetch = self._fetches[kind] = _Fetch(kind, query)
        if running is not None:
            running.cancel.set()
        try:
            started = self._Submit(fetch, fetchRows)
        except Exception:
            self._Abandon(fetch, running)
            raise
        if not started:
            self._Abandon(fetch, running)
            if self._fetcher is None:
                logger.warning("Set selection: no fetcher registered")
            elif _Stopping():
                logger.info("Set selection: the app is quitting, not fetching")
            else:
                logger.info("Set selection: no tournament set, not fetching")
            return NO_TOURNAMENT
        self._Announce(fetch)
        return STARTED

    def _Abandon(self, fetch, replaced):
        """A fetch that never started: as if it had not been asked for. The one
        it replaced was told to stop, and with no tournament left to fetch from
        goes no further, so what it published is taken back."""
        with StateManager.lock:
            with self._lock:
                if not self._Current(fetch):
                    return
                del self._fetches[fetch.kind]
            if replaced is not None:
                self._SetIdle(fetch.kind)

    def _Submit(self, fetch, fetchRows) -> bool:
        """Run the fetch on a job of the fetcher's. Counted alive from before
        it is submitted until it is over. True if it was started."""
        with self._lock:
            self._alive[fetch.kind] += 1
        started = False
        try:
            if self._fetcher is not None:
                started = bool(self._fetcher(
                    lambda provider: self._RunJob(fetch, fetchRows, provider),
                    None))
        finally:
            if not started:
                self._JobOver(fetch.kind)
        return started

    def _JobOver(self, kind):
        with self._lock:
            self._alive[kind] -= 1
            if self._alive[kind] <= 0:
                del self._alive[kind]

    def _RunJob(self, fetch, fetchRows, provider):
        try:
            self._Job(fetch, fetchRows, provider)
        except Exception:
            # Its end could not be published: once more, so that it is not
            # left saying running.
            logger.exception("Set selection: the fetch failed")
            try:
                with self._lock:
                    if not self._Current(fetch):
                        return
                    if not fetch.over:
                        fetch.status, fetch.error = ERROR, FETCH_FAILED
                        fetch.over = True
                self._Publish(fetch)
            except Exception:
                logger.exception(
                    "Set selection: the end of the fetch could not be "
                    "published")
        finally:
            self._JobOver(fetch.kind)

    def _Job(self, fetch, fetchRows, provider):
        """The fetch, then its one answer. A job of the task executor, and
        never waits for another one."""
        with self._lock:
            if not self._Current(fetch):
                return
        url = getattr(provider, "url", "")
        try:
            rows, failed = fetchRows(fetch, provider)
        except Exception:
            logger.exception(f"Set selection: {fetch.kind} could not be "
                             "fetched")
            rows, failed = None, 0
        if _Stopping():
            # What a request the shutdown cut short answered is no answer.
            return
        with self._lock:
            if not self._Current(fetch):
                # Replaced, stopped or forgotten meanwhile.
                return
            fetch.over = True
            if rows is None:
                fetch.status, fetch.error = ERROR, FETCH_FAILED
            else:
                fetch.status = DONE
                fetch.rows = rows
                fetch.failed = failed
                fetch.url = url if isinstance(url, str) else ""
                fetch.fetchedAt = int(time.time())
        self._Publish(fetch)


def _FollowCandidates(rows):
    """The phases a fetch answered are the match bar's pool names too
    (MatchNameCandidates.FollowPhases, ADR-005 S4x review): until then only
    the provider adapter's own fetch (tournament_phases_updated) reached
    them, so GET /match-names/candidates and the Web's combos missed what
    v1's phases:refresh fetched. The rows keep what FollowPhases reads
    (`groups[].name`).

    Called with the end of the current fetch published, inside
    StateManager.lock and outside the service's: a tournament change
    (Forget takes StateManager.lock) comes after it -- and the adapter's
    empty list after that -- or has dropped the fetch, whose answer then
    comes here no more. The candidates' lock guards their own fields only.
    The desktop's combos are not told: they read the lists again on the
    adapter's two signals, as they did (the set window's phase fetch, a
    tournament load)."""
    candidates = MatchNameCandidates.instance
    if candidates is None:
        return
    try:
        candidates.FollowPhases(rows)
    except Exception:
        logger.exception("Set selection: the match bar's pool names were "
                         "not updated")


def _FetchPhases(fetch, provider):
    return PhaseRows(provider.GetTournamentPhases(strict=True)), 0


def _FetchStations(fetch, provider):
    return StationRows(provider.GetStations(strict=True)), 0


def _FetchSets(service, fetch, provider, groupIds, finished):
    missing = []
    answers = provider.GetMatches(
        getFinished=finished, phaseGroupIds=groupIds,
        progress_callback=lambda done, total: service._Progress(
            fetch, done, total),
        cancel_event=fetch.cancel, strict=True, failed_pages=missing.append)
    rows = [SetRow(answer) for answer in answers]
    read = [row for row in rows if row is not None]
    return read, sum(missing) + len(rows) - len(read)


def _Stopping():
    executor = TaskExecutor.instance
    return executor is not None and executor.stopping.is_set()
