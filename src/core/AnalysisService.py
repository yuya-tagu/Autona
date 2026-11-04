"""The tournament's match list (この大会の試合一覧, ADR-005 S4j).

Every match of every phase group of the loaded tournament, one row each:
the round, both sides' seed and name, the score, and three numbers worked
out of the bracket -- the upset factor, the winner's losers run and the
loser's seed over. Until S4j all of it was the STATS panel's sub-tab
(the Qt AutonaAnalysisWidget, removed in S4k): the fetch, its parallelism,
the rows and their drop on a tournament switch, kept in the widget's
memory -- so nothing else could see the rows, and a headless shell could
not make them. Since S4k the Statistics page (web/src/StatsPage) shows
them: it starts a fetch (POST /api/v1/analysis:refresh), follows
``analysis`` and reads the rows (GET /api/v1/analysis).

The fetch: ``Refresh()`` answers ``STARTED`` / ``BUSY`` (one is in
progress; this request is dropped, not queued) / ``NO_TOURNAMENT``. It runs
through the injected ``fetcher`` (the provider's ``RunWithProvider``,
registered by ``bootstrap_core()``, so the headless shell fetches too):

1. a first job fetches the tournament's phases -- every time: the phases
   are the tournament's as it is now, and nothing waits for a signal's
   cache -- and lines up every phase group with an id in one queue (a
   group listed twice, once);
2. the groups are fetched by up to ``PARALLEL_FETCHES`` jobs taking from
   that queue (two of the job lane's four threads, ADR-005 S3f-2 decision
   7) -- fewer while jobs of a dropped fetch are still running, never
   fewer than one. The first job goes on with the queue; the others are
   started from the main thread (the injected ``later``), because a job
   may not submit a job (TaskExecutor). No job ever waits for another one;
3. whichever job records the last answer builds the rows
   (``BuildRows``: ``core.Bracket`` and the three numbers) and publishes
   the end.

The provider is asked strictly (``strict=True``): a request that failed
raises instead of answering empty, so that a failed fetch is told from a
tournament with no phase group, and a failed group from one with no sets
(S4j review M1).

How it is going is published in the state, ``analysis`` --
``{status, error, done, total, failed, revision}`` (StateSchema.
ReadAnalysis): ``running`` (``total`` 0 while the phases are fetched),
``done``, or ``error`` -- ``no_phases`` (the tournament has no phase
group) or ``fetch_failed`` (the phases request failed, or every group's
did). Every fetch takes the next revision,
published before ``Refresh`` returns. The rows are not in the state (a
tournament has hundreds to over a thousand of them): they stay in this
service's memory with the URL of the tournament they are from, read by
``Result()`` / ``Rows()`` (``ResultRevision()``: which rows they are,
without copying them). A fetch that ends in an error keeps the rows of the
last one that did not (the Qt panel's table did).

``Forget()``: the provider adapter calls it when the tournament is loaded
(again) or unloaded -- directly, not through a state observer. The fetch in
progress is dropped (its jobs stop at their next step, and what they still
get back is not recorded: each fetch is a generation), the rows are
dropped, and ``analysis`` goes back to idle at the next revision.

Each row (``BracketRows``) is a structure, not a table's text: ``round``
(the round's name, after the phase / group when there are several groups)
and ``roundOrder`` (``[group id, round, index in the round]``: the Round
column's sort key), ``p1`` / ``p2`` (``{"seed": n or None, "name": "" when
unknown}``), ``score`` (``[a, b]``; None until the set is finished),
``upset`` (None unless the set was won), ``losersRun`` (0: none) and
``seedOver`` (None: none). "TBD", "(seed N)", "-" are the page's wording.

Lock order: ``StateManager.lock``, then this service's own lock, which is
held only around its own fields: the state is written, a fetch made and the
fetcher / ``later`` called with it released.

The round names are the program locale's, injected (``roundNames``, read
at each build: core may not import the locale tables). Qt-free
(tests/test_core_qt_free.py). Import side effects: none (agent-pitfalls
#1). ``AnalysisService.instance`` is built by ``bootstrap_core()``.
"""
import collections
import copy
import threading

from loguru import logger

from . import StateSchema as schema
from .Bracket import Bracket, BracketSet
from .StateManager import StateManager
from .TaskExecutor import TaskExecutor

PATH = schema.ANALYSIS_ROOT

# What Refresh answers.
STARTED = "started"
BUSY = "busy"
NO_TOURNAMENT = "no tournament"

# analysis.status
IDLE = "idle"
RUNNING = "running"
DONE = "done"
ERROR = "error"
# analysis.error, with ERROR ("" otherwise).
NO_PHASES = "no_phases"
FETCH_FAILED = "fetch_failed"

# The most phase groups fetched at the same time -- the jobs one fetch runs
# (S3f-2 decision 7: the job lane has four threads that every tournament
# fetch shares). A fetch runs this many less the jobs of dropped fetches
# still running (their requests are still out), and always at least one:
# with 2, a second job only while no dropped job is left.
PARALLEL_FETCHES = 2


# ── the rows ─────────────────────────────────────────────────────────────

def RoundIndex(placement):
    """Double elimination placement -> the index of the round a player of
    that seed is expected to go out in."""
    if placement is None or placement < 1:
        return 0
    if placement == 1:
        return 0
    k = (placement - 1).bit_length()
    if 4 * placement <= 3 * (1 << k):
        return 2 * (k - 1)
    return 2 * (k - 1) + 1


def UpsetFactor(winnerSeed, loserSeed):
    """How many rounds earlier the winner was expected to go out than the
    loser (0 when a seed is unknown). Not StatsService's placement math: the
    match list's own measure, unchanged by S4j."""
    if winnerSeed is None or loserSeed is None:
        return 0
    if winnerSeed <= 0 or loserSeed <= 0:
        return 0
    return RoundIndex(winnerSeed) - RoundIndex(loserSeed)


def PhaseGroupEntries(phases):
    """Every phase group with an id, in Start.gg's order:
    [(phase name, group id, group name)]. A group listed more than once is
    there once, where it is first listed (the pre-S4j panel kept one result
    per id, and counted the groups by them)."""
    entries = []
    seen = set()
    for phase in phases if isinstance(phases, list) else []:
        if not isinstance(phase, dict):
            continue
        for group in phase.get("groups") or []:
            if not isinstance(group, dict) or group.get("id") is None:
                continue
            groupId = group.get("id")
            if groupId in seen:
                continue
            seen.add(groupId)
            entries.append((phase.get("name") or "", groupId,
                            group.get("name") or ""))
    return entries


def RoundPrefix(phaseName, groupName):
    """What a round's name follows when the list has several groups: the
    phase and the group ("Pools / Pool A1 · "), the phase alone when its
    name says the group's, else whichever has a name."""
    if phaseName and groupName and groupName not in phaseName:
        return f"{phaseName} / {groupName} · "
    return f"{phaseName or groupName} · "


def _EntrantName(entrants, seed):
    if not isinstance(seed, int) or seed <= 0 or seed > len(entrants):
        return ""
    entrant = entrants[seed - 1] or {}
    if entrant.get("name"):
        return entrant["name"]
    players = entrant.get("players") or []
    if players:
        return (players[0] or {}).get("gamerTag") or ""
    return ""


def _RoundName(bracket, roundKey, roundNumber):
    try:
        return bracket.GetRoundName(roundKey) or str(roundNumber)
    except Exception:
        # The round names were not loaded (an empty table formats nothing).
        logger.opt(exception=True).warning(
            f"Analysis: no name for the round {roundKey}")
        return str(roundNumber)


def BracketRows(bracket, entrants, roundPrefix, groupId):
    """The rows of one phase group's bracket (see the module docstring), in
    the bracket's order: its rounds as Start.gg listed them, each round's
    sets in order. A set with a bye, or with neither side known yet, has no
    row."""
    # (id(next set), its slot) -> the set whose winner goes there: walked
    # back from a losers set to the losers wins its winner came through.
    winSource = {}
    for sets in bracket.rounds.values():
        for bs in sets:
            if bs.winNext is not None:
                winSource[(id(bs.winNext), bs.winNextSlot)] = bs

    # Where a set's loser places: 2 for the sets nothing follows (the last
    # set of the winners side), then the losers rounds from the last one
    # back, a round's sets sharing the place right after the round before.
    placements = {}
    positiveRounds = sorted(
        int(r) for r in bracket.rounds.keys() if int(r) > 0)
    negativeRounds = sorted(
        (int(r) for r in bracket.rounds.keys() if int(r) < 0),
        key=abs, reverse=True)
    for roundNumber in positiveRounds:
        for bs in bracket.rounds.get(str(roundNumber), []):
            if bs.winNext is None and bs.loseNext is None:
                placements[id(bs)] = 2
    nextPlace = 3
    for roundNumber in negativeRounds:
        real = [bs for bs in bracket.rounds.get(str(roundNumber), [])
                if not bs.isByeSet
                and bs.playerIds[0] != BracketSet.BYE
                and bs.playerIds[1] != BracketSet.BYE]
        if not real:
            continue
        for bs in real:
            placements[id(bs)] = nextPlace
        nextPlace += len(real)

    def losersRun(bs):
        # The losers sets the winner of this losers set won in a row to get
        # here.
        if bs.pos[0] >= 0 or not bs.finished:
            return 0
        scores = bs.score or [0, 0]
        if scores[0] == scores[1]:
            return 0
        winnerSlot = 0 if scores[0] > scores[1] else 1
        winner = bs.playerIds[winnerSlot]
        run = 0
        current, slot = bs, winnerSlot
        visited = set()
        while id(current) not in visited:
            visited.add(id(current))
            source = winSource.get((id(current), slot))
            if source is None or not source.finished:
                break
            sourceScores = source.score or [0, 0]
            if sourceScores[0] == sourceScores[1]:
                break
            sourceSlot = 0 if sourceScores[0] > sourceScores[1] else 1
            if source.playerIds[sourceSlot] != winner:
                break
            if source.pos[0] >= 0:
                break
            run += 1
            current, slot = source, sourceSlot
        return run

    rows = []
    for roundKey, sets in bracket.rounds.items():
        try:
            roundNumber = int(roundKey)
        except (TypeError, ValueError):
            continue
        roundName = _RoundName(bracket, roundKey, roundNumber)
        for index, bs in enumerate(sets):
            ids = bs.playerIds or [-1, -1]
            if any(i == BracketSet.BYE for i in ids):
                continue
            if ids[0] == BracketSet.PENDING and ids[1] == BracketSet.PENDING:
                continue
            seeds = [i if isinstance(i, int) and i > 0 else None
                     for i in ids[:2]]
            scores = bs.score or [0, 0]
            finished = bool(bs.finished)
            decided = finished and scores[0] != scores[1]

            upset = None
            seedOver = None
            if decided:
                won = 0 if scores[0] > scores[1] else 1
                winnerSeed, loserSeed = seeds[won], seeds[1 - won]
                upset = UpsetFactor(winnerSeed, loserSeed)
                placement = placements.get(id(bs))
                if (placement is not None and loserSeed
                        and loserSeed > placement):
                    seedOver = loserSeed - placement

            rows.append({
                "round": f"{roundPrefix}{roundName}" if roundPrefix
                else str(roundName),
                "roundOrder": [str(groupId), roundNumber, index],
                "p1": {"seed": seeds[0],
                       "name": _EntrantName(entrants, ids[0])},
                "p2": {"seed": seeds[1],
                       "name": _EntrantName(entrants, ids[1])},
                "score": [scores[0], scores[1]] if finished else None,
                "upset": upset,
                "losersRun": losersRun(bs),
                "seedOver": seedOver,
            })
    return rows


def _HasSets(answer):
    return isinstance(answer, dict) and bool(answer.get("sets"))


def BuildRows(entries, answers, matchNames=None):
    """The rows of every phase group: `entries` as PhaseGroupEntries gives
    them, `answers` what each one's fetch returned (None: its request
    failed). Returns (rows, failed, answered): `failed` the groups that
    gave no rows -- no answer, no sets, or sets no rows could be made of
    (the bracket or its rows raised: that group's, the others' stay) --
    and `answered` the groups that answered anything (none: the fetch
    failed). A group's rounds follow its phase and group names when there
    are several groups."""
    several = len(entries) > 1
    rows = []
    failed = 0
    answered = 0
    for (phaseName, groupId, groupName), answer in zip(entries, answers):
        if answer:
            answered += 1
        if not _HasSets(answer):
            failed += 1
            continue
        prefix = RoundPrefix(phaseName, groupName) if several else ""
        try:
            bracket = Bracket(answer, matchNames=matchNames)
            groupRows = BracketRows(
                bracket, answer.get("entrants") or [], prefix, groupId)
        except Exception:
            logger.opt(exception=True).warning(
                f"Analysis: no rows could be made of the group {groupId}")
            failed += 1
            continue
        rows.extend(groupRows)
    return rows, failed, answered


# ── the fetch ────────────────────────────────────────────────────────────

class _Fetch:
    """One Refresh: its generation's progress, answers and rows. Its fields
    are the service's lock's."""

    def __init__(self, generation):
        self.generation = generation
        # Set when Refresh publishes it (None until then: nothing of it is
        # published before).
        self.revision = None
        self.provider = None
        self.url = ""
        self.entries = None          # None until the phases came
        self.answers = []
        self.queue = collections.deque()   # indexes into entries
        self.done = 0
        self.failed = 0
        self.status = RUNNING
        self.error = ""
        self.rows = None
        self.over = False

    def Snapshot(self):
        return {"status": self.status, "error": self.error,
                "done": self.done, "total": len(self.entries or ()),
                "failed": self.failed, "revision": self.revision}


class AnalysisService:
    """The match list (see the module docstring)."""

    instance: "AnalysisService" = None

    def __init__(self, fetcher=None, later=None, roundNames=None):
        """`fetcher(job, status)` runs `job(provider)` on a job of the task
        executor with the tournament loaded now and returns True, or returns
        False, running nothing, when there is none (the provider adapter's
        RunWithProvider). `later(fn)` runs `fn` on the main thread once its
        event loop is back (the GUI executor's later): how the jobs after
        the first are started. `roundNames()` the localized round names
        core.Bracket names the rounds with."""
        self._fetcher = fetcher
        self._later = later
        self._roundNames = roundNames
        self._lock = threading.Lock()
        self._generation = 0
        # The newest fetch (over or not); None once forgotten.
        self._fetch = None
        # The newest fetch that ended with rows, once published.
        self._result = None
        # generation -> its jobs submitted and not over (a dropped fetch's
        # too: their requests are still out).
        self._alive = collections.Counter()

    # ── reading ──────────────────────────────────────────────────────────

    @staticmethod
    def Progress() -> dict:
        """`analysis` as published (StateSchema.ReadAnalysis). Any
        thread."""
        return schema.ReadAnalysis()

    def Running(self) -> bool:
        """Whether a fetch is in progress."""
        with self._lock:
            return self._fetch is not None and not self._fetch.over

    def Result(self) -> dict:
        """The rows of the newest fetch that made them: {"url": the
        tournament's, "rows": [...] (a copy), "revision": that fetch's}.
        No rows ({"url": "", "rows": [], "revision": None}) before the first
        one, and once forgotten. Any thread."""
        with self._lock:
            fetch = self._result
            if fetch is None:
                return {"url": "", "rows": [], "revision": None}
            url, rows, revision = fetch.url, fetch.rows, fetch.revision
        # Never changed once made: copied out of the lock.
        return {"url": url, "rows": copy.deepcopy(rows), "revision": revision}

    def ResultRevision(self):
        """Result()'s revision (None: no rows), without copying the rows:
        whether the rows a reader has are still these. Any thread."""
        with self._lock:
            return None if self._result is None else self._result.revision

    def Rows(self) -> list:
        """Result()'s rows."""
        return self.Result()["rows"]

    # ── fetching ─────────────────────────────────────────────────────────

    def Refresh(self) -> str:
        """Fetch the match list of the tournament loaded now (see the module
        docstring). STARTED, its progress published at the next revision
        before this returns; BUSY when one is in progress (nothing changes);
        NO_TOURNAMENT when none is loaded, the app is quitting or no shell
        registered a fetcher (nothing changes). Any thread but a job's."""
        with self._lock:
            if self._fetch is not None and not self._fetch.over:
                logger.info("Analysis: a fetch is already in progress")
                return BUSY
            self._generation += 1
            fetch = self._fetch = _Fetch(self._generation)
        try:
            started = self._Submit(fetch, self._FirstJob)
        except Exception:
            self._Abandon(fetch)
            raise
        if not started:
            self._Abandon(fetch)
            if self._fetcher is None:
                logger.warning("Analysis: no fetcher registered")
            elif _Stopping():
                logger.info("Analysis: the app is quitting, not fetching")
            else:
                logger.info("Analysis: no tournament set, not fetching")
            return NO_TOURNAMENT
        self._Announce(fetch)
        return STARTED

    def Forget(self):
        """Drop the fetch in progress and the rows: the tournament they are
        from is no longer the one loaded. `analysis` goes back to idle at
        the next revision -- even from idle: a reader that showed something
        of the tournament before (the page's "no tournament") sees that the
        tournament changed. Main thread: the provider adapter's
        LoadTournament / UnloadTournament, once the new provider (or none)
        is in place -- a Refresh made before this is dropped by it, and one
        made after it fetches from the new tournament."""
        with StateManager.lock:
            published = schema.ReadAnalysis()
            with self._lock:
                self._generation += 1
                dropped = self._fetch is not None or self._result is not None
                self._fetch = None
                self._result = None
            self._Set(schema.DefaultAnalysis(published["revision"] + 1))
        if dropped:
            logger.info("Analysis: the match list was dropped")

    # ── the published progress ───────────────────────────────────────────

    @staticmethod
    def _Set(value):
        # One write of the whole dict: a reader never sees half of it.
        StateManager.SetIfChanged(PATH, value)

    def _Current(self, fetch):
        # Under self._lock.
        return fetch is self._fetch and fetch.generation == self._generation

    def _Abandon(self, fetch):
        # A fetch that never started: as if it had not been asked for.
        with self._lock:
            if self._fetch is fetch:
                self._fetch = None

    def _Announce(self, fetch):
        """Publish a started fetch at the next revision -- what its jobs
        did meanwhile included (they publish nothing before this)."""
        with StateManager.lock:
            revision = schema.ReadAnalysis()["revision"] + 1
            with self._lock:
                if not self._Current(fetch):
                    return
                fetch.revision = revision
            self._Publish(fetch)

    def _Publish(self, fetch):
        """Publish `fetch`'s progress as it is now, if it is the current
        fetch and has been announced. Its rows become Result()'s with the
        end that published them. Whoever publishes last publishes the latest
        progress: it is read in the same hold of StateManager.lock as the
        write."""
        with StateManager.lock:
            with self._lock:
                if not self._Current(fetch) or fetch.revision is None:
                    return
                value = fetch.Snapshot()
                if fetch.over and fetch.status == DONE:
                    self._result = fetch
            self._Set(value)

    # ── the jobs ─────────────────────────────────────────────────────────

    def _Submit(self, fetch, job, provider=None):
        """Run `job(fetch, provider)` on a job of the fetcher's -- with the
        provider the fetcher hands it when `provider` is None. Counted
        alive (_Width) from before it is submitted until it is over. True
        if it was started."""
        with self._lock:
            self._alive[fetch.generation] += 1
        started = False
        try:
            if self._fetcher is not None:
                started = bool(self._fetcher(
                    lambda given: self._RunJob(
                        job, fetch, given if provider is None else provider),
                    None))
        finally:
            if not started:
                self._JobOver(fetch.generation)
        return started

    def _RunJob(self, job, fetch, provider):
        try:
            self._Guarded(job, fetch, provider)
        finally:
            self._JobOver(fetch.generation)

    def _JobOver(self, generation):
        with self._lock:
            self._alive[generation] -= 1
            if self._alive[generation] <= 0:
                del self._alive[generation]

    def _Width(self, fetch):
        """How many jobs `fetch` may run: PARALLEL_FETCHES less the jobs of
        dropped fetches still running, at least one. Under self._lock."""
        stale = sum(count for generation, count in self._alive.items()
                    if generation != fetch.generation)
        return max(1, PARALLEL_FETCHES - stale)

    def _Guarded(self, job, fetch, provider):
        """`job(fetch, provider)`; one that raises ends the fetch in an
        error -- or, when it was already over (its end's publish raised),
        publishes that end again -- so that it is not left in progress
        (every later Refresh would answer BUSY, and the state would say
        running)."""
        try:
            job(fetch, provider)
        except Exception:
            logger.exception("Analysis: the fetch failed")
            with self._lock:
                if not self._Current(fetch):
                    return
                if not fetch.over:
                    fetch.status, fetch.error = ERROR, FETCH_FAILED
                    fetch.over = True
            try:
                self._Publish(fetch)
            except Exception:
                logger.exception(
                    "Analysis: the end of the fetch could not be published")

    def _FirstJob(self, fetch, provider):
        """The phases, the queue of groups, the other jobs; then the groups
        like the others. A job of the task executor."""
        # No phase group: the tournament has none (no_phases), or the
        # request for them failed (fetch_failed).
        noEntries = NO_PHASES
        try:
            phases = provider.GetTournamentPhases(strict=True)
        except Exception:
            logger.exception("Analysis: the phases could not be fetched")
            phases = None
            noEntries = FETCH_FAILED
        if _Stopping():
            return
        entries = PhaseGroupEntries(phases)
        with self._lock:
            if not self._Current(fetch):
                return
            fetch.provider = provider
            url = getattr(provider, "url", "")
            fetch.url = url if isinstance(url, str) else ""
            fetch.entries = entries
            fetch.answers = [None] * len(entries)
            fetch.queue = collections.deque(range(len(entries)))
            if not entries:
                fetch.status, fetch.error = ERROR, noEntries
                fetch.over = True
            more = min(self._Width(fetch), len(entries)) - 1
        if not entries:
            logger.info(f"Analysis: no phase group to fetch ({noEntries})")
            self._Publish(fetch)
            return
        self._Publish(fetch)
        for _ in range(more):
            self._StartAnotherJob(fetch)
        self._TakeFromQueue(fetch, provider)

    def _StartAnotherJob(self, fetch):
        # A job may not submit one (TaskExecutor's nesting rule): the main
        # thread does, once its event loop is back.
        if self._later is None:
            return
        try:
            self._later(lambda: self._AnotherJob(fetch))
        except Exception:
            # The jobs running take the whole queue.
            logger.exception("Analysis: another fetch could not be queued")

    def _AnotherJob(self, fetch):
        """On the main thread: start one more job, while the fetch still has
        groups queued and runs fewer jobs than it may (_Width: a dropped
        fetch's job may still be out). It fetches from the tournament the
        phases came from."""
        with self._lock:
            if not self._Current(fetch) or fetch.over or not fetch.queue:
                return
            if self._alive[fetch.generation] >= self._Width(fetch):
                return
            provider = fetch.provider
        try:
            self._Submit(fetch, self._TakeFromQueue, provider)
        except Exception:
            logger.exception("Analysis: another fetch could not start")

    def _TakeFromQueue(self, fetch, provider):
        """Fetch queued groups until none is left. The job that records the
        last answer builds the rows."""
        while True:
            if _Stopping():
                return
            with self._lock:
                if not self._Current(fetch) or fetch.over or not fetch.queue:
                    return
                index = fetch.queue.popleft()
                groupId = fetch.entries[index][1]
            answer = _FetchGroup(provider, groupId)
            if _Stopping():
                # What a request the shutdown cut short answered is no
                # answer.
                return
            with self._lock:
                if not self._Current(fetch) or fetch.over:
                    return
                fetch.answers[index] = answer
                fetch.done += 1
                if not _HasSets(answer):
                    fetch.failed += 1
                last = fetch.done == len(fetch.entries)
            if last:
                self._Complete(fetch)
            else:
                self._Publish(fetch)

    def _Complete(self, fetch):
        with self._lock:
            if not self._Current(fetch) or fetch.over:
                return
            entries, answers = fetch.entries, list(fetch.answers)
        try:
            matchNames = self._roundNames() if self._roundNames else None
        except Exception:
            logger.exception("Analysis: the round names could not be read")
            matchNames = None
        rows, failed, answered = BuildRows(entries, answers, matchNames)
        with self._lock:
            if not self._Current(fetch) or fetch.over:
                return
            fetch.rows = rows
            fetch.failed = failed
            if answered:
                fetch.status = DONE
            else:
                fetch.status, fetch.error = ERROR, FETCH_FAILED
            fetch.over = True
        if answered:
            logger.info(f"Analysis: {len(rows)} matches "
                        f"({failed} of {len(entries)} groups without any)")
        else:
            logger.warning("Analysis: no phase group answered")
        self._Publish(fetch)


def _FetchGroup(provider, groupId):
    """What the provider answers for one group, asked strictly; None: its
    request failed."""
    fetch = getattr(provider, "GetAnalysisPhaseGroup", None)
    if fetch is None:
        logger.warning("Analysis: the provider cannot fetch a phase group")
        return None
    try:
        return fetch(groupId, strict=True)
    except Exception:
        logger.exception(f"Analysis: the group {groupId} could not be fetched")
        return None


def _Stopping():
    executor = TaskExecutor.instance
    return executor is not None and executor.stopping.is_set()
