"""Fixtures for the live start.gg integration layer.

These tests drive the real thing: an actual finished tournament on
start.gg, fetched over the network by the same StartGGDataProvider the app
uses, fed into real scoreboard widgets inside the L2 harness
(tests/conftest.py `autonaApp`), exported to real files, and read back.
Nothing here is stubbed -- the point is to find the failures that only the
real API's data shapes produce.

Two things keep that affordable:

- **Opt-in.** Without AUTONA_LIVE_TESTS=1 the whole package is skipped, so
  the default suite stays offline, fast and deterministic.
- **One fetch per module.** `liveEvent` is module-scoped and does every
  network call once (tournament, phases, the full set list, one set
  detail); the tests share its result. Start.gg's documented budget is 80
  requests/minute and this run costs about six.

The event is a real, finished Smash Ultimate tournament. Finished matters:
its sets, scores and entrants never change again, so an assertion about
them is a fact rather than a race with whoever is playing right now.
"""
import copy
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

LIVE_ENV_VAR = "AUTONA_LIVE_TESTS"
LIVE_ENABLED = os.environ.get(LIVE_ENV_VAR) == "1"

# 渋谷BeeSmash 53 / Singles -- 16 entrants, single pool, double elimination,
# played offline and long over.
EVENT_URL = "https://www.start.gg/tournament/beesmash-53/event/singles"
EVENT_SLUG = "tournament/beesmash-53/event/singles"
GQL_ENDPOINT = "https://www.start.gg/api/-/gql"

# Same headers StartGGDataProvider.QueryRequests sends. Used only by the
# reachability probe below, which deliberately does NOT go through
# QueryRequests: that retries five times with 1/2/4/8s backoff, so a machine
# with no network would spend ~15s per call before every fixture gave up.
_PROBE_HEADERS = {
    "client-version": "20",
    "Content-Type": "application/json",
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 "
        "Mobile Safari/537.36"),
}

live_only = pytest.mark.skipif(
    not LIVE_ENABLED,
    reason=f"live start.gg tests are opt-in: set {LIVE_ENV_VAR}=1")


def _requireStartGG():
    """Skip (not fail) when start.gg cannot be reached at all.

    A single attempt with a short timeout. An unreachable API is an
    environment problem, and reporting it as a test failure would train
    everyone to ignore this suite.
    """
    import requests

    try:
        response = requests.post(
            GQL_ENDPOINT, timeout=15, headers=dict(_PROBE_HEADERS),
            json={
                "operationName": "TournamentDataQuery",
                "variables": {"eventSlug": EVENT_SLUG},
                "query": "query TournamentDataQuery($eventSlug: String!)"
                         " { event(slug: $eventSlug) { id } }",
            })
    except Exception as error:
        pytest.skip(f"start.gg is unreachable ({type(error).__name__}: {error})")

    if response.status_code != 200:
        pytest.skip(f"start.gg answered HTTP {response.status_code}")
    if not response.json().get("data", {}).get("event", {}):
        pytest.skip(f"start.gg no longer serves the pinned event {EVENT_SLUG}")


@pytest.fixture(scope="module")
def liveEvent(_autonaRuntime):
    """Everything the real API says about the pinned event, fetched once.

    Depends on the session harness because the provider's parse path reads
    the bootstrapped singletons (round-name tables, the game asset manager,
    the country table) -- constructing it against a half-booted process
    would test the stubs, not the app.
    """
    if not LIVE_ENABLED:
        pytest.skip(f"set {LIVE_ENV_VAR}=1 to run the live tests")

    _requireStartGG()

    import requests

    from src.TournamentDataProvider.StartGGDataProvider import (
        StartGGDataProvider)

    # GetMatch fans its two APIs out to the session's task executor and
    # blocks until both land (ADR-005 S3f-3): the provider has no pool of
    # its own.
    provider = StartGGDataProvider(EVENT_URL, None)

    # Order matters: GetTournamentData caches numEntrants on the provider,
    # and that headcount anchors the TOP-N round naming every later set
    # parse goes through.
    tournament = provider.GetTournamentData()
    if not tournament.get("tournamentName"):
        pytest.skip("start.gg returned no tournament data for the pinned event")

    phases = provider.GetTournamentPhases()
    sets = provider.GetMatches(getFinished=True)

    # The set count start.gg itself reports for the same filters, so the
    # "no page was silently dropped" assertion has something to compare
    # against. perPage=1 keeps this cheap.
    countPage = provider.QueryRequests(
        GQL_ENDPOINT,
        type=requests.post,
        jsonParams={
            "operationName": "EventMatchListQuery",
            "variables": {
                "filters": {"state": [1, 6, 2, 3], "hideEmpty": True},
                "eventSlug": EVENT_SLUG,
                "page": 1,
                "perPage": 1,
            },
            "query": StartGGDataProvider.SetsQuery,
        })
    reportedTotal = (countPage.get("data", {}).get("event", {})
                     .get("sets", {}).get("pageInfo", {}).get("total"))

    representative = _pickSet(sets)
    match = legacy = None
    if representative:
        match = provider.GetMatch(representative["id"])
        # The legacy (gg_api) half on its own. GetMatch merges it with the
        # GraphQL half and then deliberately drops some of it, so the tests
        # that pin *why* need to see it unmerged.
        legacy = provider._GetMatchTasks(
            representative["id"], progress_callback=None, cancel_event=None)

    return SimpleNamespace(
        url=EVENT_URL,
        provider=provider,
        tournament=tournament,
        phases=phases,
        sets=sets,
        reportedTotal=reportedTotal,
        representative=representative,
        match=match,
        legacy=legacy,
    )


def _pickSet(sets):
    """One set to drive the scoreboard with, chosen deterministically.

    The grand final if the bracket has one -- it is guaranteed to be played,
    to have two named entrants and to be the set an operator would actually
    put on stream. Otherwise the lowest set id, which is stable across runs
    in a way "the first node" is not (the list is MAGIC-sorted by live
    relevance).
    """
    playable = [s for s in sets if s.get("p1_name") and s.get("p2_name")]
    if not playable:
        return None
    finals = [s for s in playable if s.get("round_name_en") == "Grand Final"]
    if finals:
        return finals[0]
    return min(playable, key=lambda s: s.get("id"))


@pytest.fixture
def realSetOnBoard(autonaApp, liveEvent):
    """Board 1 with the real set loaded, the way the app loads one.

    Mirrors AutonaTournamentDataProvider.GetMatch's result handler: the
    provider result is a plain dict, and the handler stamps overwrite/no_mains
    onto it before it reaches the widget. Without `overwrite` ChangeSetData
    only applies the mains, and the entrant names never land.

    The data is deep-copied per test because SetData writes back into the
    dict it is handed (censored tags), and the fetch is shared module-wide.
    """
    if liveEvent.match is None:
        pytest.skip("no usable set in the pinned event")

    # Character combos sit at index -1 against an empty roster, which makes
    # every "which slot got which character" assertion vacuous. The harness
    # roster is synthetic (the runtime user_data/games is empty by design);
    # what is under test here is which state keys the real payload fills.
    autonaApp.loadCharacters()

    board = autonaApp.board(1)
    data = copy.deepcopy(liveEvent.match)
    data.update({"overwrite": True, "no_mains": False})
    board.ChangeSetData(data)
    autonaApp.settle()
    return SimpleNamespace(board=board, data=data)


@pytest.fixture
def committedBoard(autonaApp):
    """Publish board 1's held subtree so ./out reflects it.

    AutonaScoreboardWidget.__init__ registers HoldSubtree("score.{n}"), so
    every write under it is buffered until the operator presses 反映.
    Anything asserting on exported files has to commit first, exactly like
    the button does.
    """
    def commit(number=1):
        autonaApp.state.CommitHeld(f"score.{number}")
        autonaApp.settle()
    return commit


@pytest.fixture
def syntheticGame(autonaApp):
    """A non-empty `game` key, restored afterwards.

    The thumbnail refuses to render without one (prepare_data treats a
    missing game.codename as "no game selected"). The harness has no game
    installed, and StateManager state is process-global -- an unrestored
    write here would follow the session into other test modules.
    """
    from src.core.StateManager import StateManager

    previous = copy.deepcopy(StateManager.Get("game"))
    StateManager.Set("game", {
        "codename": "harness_game",
        "name": "Harness Game",
        "smashgg_id": 1386,
    })
    try:
        yield
    finally:
        StateManager.Set("game", previous if previous is not None else {})
        autonaApp.settle()


@pytest.fixture(scope="module")
def thumbnailLayout(_autonaRuntime):
    """Link the shipped thumbnail template into the runtime directory.

    ThumbnailRenderer loads "<cwd>/layout/<subdir>/thumbnail.html", and the
    harness keeps layout/ a real (empty) directory rather than a link,
    because Autona.Window seeds layout/logo.png into it. Only the two
    subtrees the template actually needs are linked, read-only, and they are
    unlinked again afterwards so the harness looks untouched to the rest of
    the session.
    """
    from tests.conftest import _link_tree

    created = []
    for relative in ("layout/thumbnail", "layout/include"):
        link = _autonaRuntime.dir / relative
        if link.exists():
            continue
        _link_tree(link, PROJECT_ROOT / relative)
        created.append(link)
    try:
        yield _autonaRuntime.dir / "layout"
    finally:
        for link in reversed(created):
            try:
                # A Windows junction is a directory entry, not a symlink;
                # rmdir removes the link without following it.
                if link.is_symlink():
                    os.unlink(link)
                else:
                    os.rmdir(link)
            except OSError:
                pass


def runRestartProbe(runtimeDir, timeout=180):
    """Re-boot the app in a second process against the same working dir.

    A restart cannot be simulated in-process: bootstrap_core() has a
    re-entrancy guard and StateManager.Initialize() only loads the persisted
    state once. See restart_probe.py.
    """
    import json

    script = Path(__file__).resolve().parent / "restart_probe.py"
    environment = dict(os.environ)
    environment["QT_API"] = "pyside6"
    environment["QT_QPA_PLATFORM"] = "offscreen"
    # The tournament name is Japanese; the probe writes JSON to stdout and
    # the default Windows console codec (cp932) would mangle or raise on it.
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"

    completed = subprocess.run(
        [sys.executable, str(script), str(runtimeDir), str(PROJECT_ROOT)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, env=environment, cwd=str(runtimeDir))

    marker = "<<<AUTONA_RESTART_PROBE>>>"
    for line in completed.stdout.splitlines():
        if line.startswith(marker):
            return json.loads(line[len(marker):]), completed

    raise AssertionError(
        "the restart probe produced no result line\n"
        f"exit={completed.returncode}\n"
        f"--- stdout ---\n{completed.stdout[-4000:]}\n"
        f"--- stderr ---\n{completed.stderr[-4000:]}")
