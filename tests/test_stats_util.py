"""The stats routes and the Qt transport of the statistics.

The routes are web operations since ADR-005 S3e-5
(`AutonaWebOperations.stats_*`, core's `StatsService` behind them); this
module drives those functions -- what every route and Socket.IO event
delegates to -- and asserts on StateManager, which is what the OBS overlays
consume. What a whole sequence of operations fetches and writes, against
the recording of the tree before the move: tests/test_stats_flow.py.

`AutonaStatsUtil` is the transport only now: one fetcher registered by
bootstrap_core() as `StatsService.fetcher`, answering on the main thread.
Upstream TSH built a single instance at import time as
`AutonaStatsUtil.instance`; the fork made it per board and dropped that
assignment, but two WebServerActions handlers kept calling
`AutonaStatsUtil.instance.signals...`, so `/scoreboard<n>-stats-recent-sets`
and `/scoreboard<n>-stats-upset-factor` raised AttributeError on None for
every request -- and ignored the board number they were given.
"""
import threading

import pytest

from src import AutonaWebOperations as WebOps
from src.AutonaStatsUtil import AutonaStatsUtil
from src.core.StatsService import StatsService


# ── the singleton must stay gone ─────────────────────────────────────────

def test_stats_util_exposes_no_class_level_instance():
    """Reintroducing `instance` would revive the None-deref path; the one
    registration is StatsService.fetcher."""
    assert not hasattr(AutonaStatsUtil, "instance")


def test_one_fetcher_serves_every_board(autonaApp):
    """No board owns a stats util any more (ADR-005 S3e-5): the statistics
    are core's, and bootstrap_core() registers the one fetcher."""
    board1 = autonaApp.board(1)
    board2 = autonaApp.board(2)

    assert isinstance(StatsService.fetcher, AutonaStatsUtil)
    assert not hasattr(board1, "stats")
    assert not hasattr(board2, "stats")


# ── upset factor ─────────────────────────────────────────────────────────

def _seed(app, board, team, value):
    app.state.Set(f"score.{board}.team.{team}.player.1.seed", value)


def test_upset_factor_route_calculates_for_the_board(autonaApp):
    """Pre-fix this raised AttributeError before touching any state."""
    autonaApp.board(1)
    _seed(autonaApp, 1, 1, 1)
    _seed(autonaApp, 1, 2, 5)

    assert WebOps.stats_upset_factor("1") == "OK"
    autonaApp.settle()

    # Double elimination, seeds 1 vs 5: |0 - (2 + 2)| = 4.
    assert autonaApp.get("score.1.upset_factor") == 4


def test_upset_factor_route_resets_when_a_seed_is_missing(autonaApp):
    autonaApp.board(1)
    _seed(autonaApp, 1, 1, 1)
    _seed(autonaApp, 1, 2, 5)
    WebOps.stats_upset_factor("1")
    autonaApp.settle()
    assert autonaApp.get("score.1.upset_factor") == 4

    # Player 2 cleared: the previous pairing's factor must not linger.
    autonaApp.state.Set("score.1.team.2.player.1.seed", None)
    WebOps.stats_upset_factor("1")
    autonaApp.settle()

    assert autonaApp.get("score.1.upset_factor") == 0


# ── recent sets ──────────────────────────────────────────────────────────

def test_recent_sets_route_settles_without_a_tournament(autonaApp):
    """No provider is loaded here, which is also the day-to-day case.

    The handler must report "done" with no sets rather than raise -- the
    provider is None until a tournament URL is set, and the route is
    reachable (overlay refresh button, external API) at any time.
    """
    autonaApp.board(1)

    assert WebOps.stats_recent_sets("1") == "OK"
    autonaApp.settle()

    assert autonaApp.get("score.1.recent_sets") == {"state": "done", "sets": []}


# ── the board number is honoured ─────────────────────────────────────────

@pytest.mark.parametrize("board", ["1", "2"])
def test_stats_routes_address_the_requested_board(autonaApp, board):
    """The `scoreboard` argument used to be accepted and then ignored."""
    autonaApp.board(int(board))
    other = "2" if board == "1" else "1"
    autonaApp.state.Unset(f"score.{other}.recent_sets")

    WebOps.stats_recent_sets(board)
    autonaApp.settle()

    assert autonaApp.get(f"score.{board}.recent_sets.state") == "done"
    assert autonaApp.get(f"score.{other}.recent_sets") is None


@pytest.mark.parametrize("call", [
    lambda: WebOps.stats_recent_sets("3"),
    lambda: WebOps.stats_upset_factor("3"),
    lambda: WebOps.stats_last_sets("3", "1"),
    lambda: WebOps.stats_history_sets("3", "both"),
])
def test_the_crew_board_has_no_statistics(autonaApp, call):
    """The legacy routes answered 500 for board 3 (its board has no stats
    util) after building it; they still answer 500 (the ValueError), and
    no longer build a board only to find out."""
    autonaApp.dropAllBoards()
    before = autonaApp.get("score.3")

    with pytest.raises(ValueError):
        call()
    autonaApp.settle()

    assert autonaApp.manager.scoreboards.get(3) is None
    assert autonaApp.get("score.3") == before


def test_the_refresh_of_the_crew_board_is_an_error_string(autonaApp):
    """What WebServerActions.refresh_stats answered for a board with no
    stats util (v1 never reaches it: require_board_12)."""
    autonaApp.dropAllBoards()

    assert WebOps.refresh_stats(3) == \
        "ERROR: scoreboard 3 does not support stats"
    assert autonaApp.manager.scoreboards.get(3) is None


# ── the socket.io wrappers call these with the right arity ───────────────

def test_socketio_stats_handlers_pass_only_the_board_number():
    """`stats_recent_sets` / `stats_upset_factor` take (scoreboard) only.

    The Socket.IO wrappers were copied from the last_sets/history_sets pair
    and kept passing `info.get("player")` as a second positional argument,
    which is a TypeError on these two.
    """
    import inspect
    import re

    from src import AutonaWebServer

    source = inspect.getsource(AutonaWebServer)
    for handler in ("stats_recent_sets", "stats_upset_factor"):
        # The web operations since ADR-005 S3e-5 (WebServerActions, reached
        # through _board_actions(), until then).
        calls = re.findall(r"WebOps\.%s\((.*?)\)\)" % handler, source)
        assert calls, f"no WebOps.{handler}(...) call found"
        for arguments in calls:
            assert "player" not in arguments, (
                f"{handler} takes only the board number, got: {arguments}")


# ── the transport ────────────────────────────────────────────────────────

def test_a_fetch_reaches_the_provider_with_its_arguments(autonaApp,
                                                        monkeypatch):
    from src.AutonaTournamentDataProvider import AutonaTournamentDataProvider
    provider = AutonaTournamentDataProvider.instance
    calls = []
    for name in ("GetRecentSets", "GetLastSets",
                 "GetPlayerHistoryStandings"):
        monkeypatch.setattr(
            provider, name,
            lambda callback, *args, name=name: calls.append((name, args)))
    fetcher = StatsService.fetcher

    fetcher.RecentSets([1, 2], [3, 4], 1386, lambda data: None)
    fetcher.LastSets([1, 2], 1, lambda data: None)
    fetcher.HistorySets([3, 4], 2, 1386, lambda data: None)

    # The team crosses as the string the provider's workers put back into
    # their answer (playerNumber), as the board's stats util passed it.
    assert calls == [
        ("GetRecentSets", ([1, 2], [3, 4], 1386)),
        ("GetLastSets", ([1, 2], "1")),
        ("GetPlayerHistoryStandings", ([3, 4], "2", 1386)),
    ]


@pytest.mark.parametrize("playerId, asked", [
    ([7, 70], 7),        # a Start.gg entrant: [player id, user id]
    (7, 7),              # a scalar (the local database, the Web)
    ("7", "7"),
], ids=["list", "int", "str"])
def test_the_provider_is_asked_for_the_player_id_of_any_shape(
        autonaApp, monkeypatch, playerId, asked):
    """The adapter indexed the id ([0]) on the caller's thread, so a scalar
    raised TypeError there -- and stopped the statistics asked for after it
    (ADR-005 S3e-5 review)."""
    from types import SimpleNamespace

    from src.AutonaTournamentDataProvider import AutonaTournamentDataProvider
    adapter = AutonaTournamentDataProvider.instance
    calls = []

    def recording(name):
        def method(**kwargs):
            calls.append((name, kwargs["playerID"]))
        return method

    monkeypatch.setattr(adapter, "provider", SimpleNamespace(
        name="StartGG", GetLastSets=recording("last"),
        GetPlayerHistoryStandings=recording("history")))
    # The pool runs the worker at once, on this thread.
    monkeypatch.setattr(adapter, "threadPool",
                        SimpleNamespace(start=lambda worker: worker.run()))

    StatsService.fetcher.LastSets(playerId, 1, lambda data: None)
    StatsService.fetcher.HistorySets(playerId, 2, 1386, lambda data: None)

    assert calls == [("last", asked), ("history", asked)]


def test_a_route_answers_ok_when_a_fetch_raises(autonaApp, monkeypatch):
    """As when the routes only emitted the board's signals: the failure is
    logged, the route answers "OK", and the other statistics are still
    asked for."""
    from tests.fixtures.stats.spies import FakeFetcher

    class Raising(FakeFetcher):
        def LastSets(self, playerId, team, onResult):
            super().LastSets(playerId, team, onResult)
            if team == 1:
                raise TypeError("the fetch")

    fetcher = Raising()
    monkeypatch.setattr(StatsService, "fetcher", fetcher)
    autonaApp.board(1)
    autonaApp.state.Set("score.1.team.1.player.1.id", [1, 10])
    autonaApp.state.Set("score.1.team.2.player.1.id", [2, 20])

    assert WebOps.stats_last_sets("1", "both") == "OK"
    assert WebOps.refresh_stats("1") == "OK"

    assert [args for kind, args in fetcher.asked()
            if kind == StatsService.LAST_SETS] == [
        ([1, 10], 1), ([2, 20], 2), ([1, 10], 1), ([2, 20], 2)]


def test_an_answer_from_a_worker_is_written_on_the_main_thread(autonaApp):
    """The provider's worker emits its answer on its own thread; the write
    happens on the main thread (the queued `answered` signal), with the
    payload as the dict it was."""
    seen = []
    callback = StatsService.fetcher._Callback(
        lambda data: seen.append((threading.current_thread(), data)))
    payload = {"playerNumber": "1", "last_sets": [{"b": 1, "a": 2}]}

    worker = threading.Thread(target=callback.emit, args=(payload,))
    worker.start()
    worker.join()
    assert seen == []
    autonaApp.settle()

    assert seen == [(threading.main_thread(), payload)]


def test_later_runs_once_the_event_loop_is_back(autonaApp):
    seen = []

    StatsService.fetcher.Later(
        lambda: seen.append(threading.current_thread()))
    assert seen == []
    autonaApp.settle()

    assert seen == [threading.main_thread()]


def test_a_failing_write_is_logged_not_raised(autonaApp):
    def failing(data):
        raise RuntimeError("boom")

    StatsService.fetcher._Callback(failing).emit({})
    StatsService.fetcher.Later(lambda: failing(None))
    autonaApp.settle()   # nothing escapes the slots


# ── late answers (ADR-005 S3e-5) ─────────────────────────────────────────

@pytest.fixture
def startgg(autonaApp, monkeypatch):
    """A loaded Start.gg tournament whose fetches never leave the process:
    each request's callback is kept in `pending` as (method, args,
    callback), to be answered by the test, from a worker thread."""
    from types import SimpleNamespace

    from src.AutonaTournamentDataProvider import AutonaTournamentDataProvider
    provider = AutonaTournamentDataProvider.instance
    pending = []
    for name in ("GetRecentSets", "GetLastSets",
                 "GetPlayerHistoryStandings"):
        monkeypatch.setattr(
            provider, name, lambda callback, *args, name=name:
            pending.append((name, args, callback)))
    monkeypatch.setattr(provider, "provider",
                        SimpleNamespace(name="StartGG", url=""))
    videogame = autonaApp.get("provider_videogame")
    autonaApp.state.Set("provider_videogame", {"id": 1386, "name": "G"})
    try:
        yield pending
    finally:
        if videogame is None:
            autonaApp.state.Unset("provider_videogame")
        else:
            autonaApp.state.Set("provider_videogame", videogame)


def _answerFromAWorker(callback, data):
    worker = threading.Thread(target=callback.emit, args=(data,))
    worker.start()
    worker.join()


def _lastSetsAsked(pending, team):
    return [callback for name, args, callback in pending
            if name == "GetLastSets" and args[1] == str(team)]


def test_a_late_answer_does_not_land_on_the_next_player(autonaApp, startgg):
    """The operator puts A in, then B, before A's sets arrive: A's answer
    is dropped, B's is written -- whichever arrives first."""
    autonaApp.board(1)
    assert WebOps.update_player(1, 2, 1, {
        "gamerTag": "OPP", "id": [3, 30]}) is True
    assert WebOps.update_player(1, 1, 1, {
        "gamerTag": "A", "id": [1, 10]}) is True
    autonaApp.settle()
    assert WebOps.update_player(1, 1, 1, {
        "gamerTag": "B", "id": [2, 20]}) is True
    autonaApp.settle()
    first, second = _lastSetsAsked(startgg, 1)[-2:]

    _answerFromAWorker(second, {"playerNumber": "1",
                                "last_sets": [{"round_name": "B's"}]})
    _answerFromAWorker(first, {"playerNumber": "1",
                               "last_sets": [{"round_name": "A's"}]})
    autonaApp.settle()

    assert autonaApp.get("score.1.last_sets.1") == {"1": {
        "phase_id": None, "phase_name": None, "round_name": "B's",
        "player_score": None, "player_seed": None, "player_team": None,
        "player_name": None, "oponent_score": None, "oponent_seed": None,
        "oponent_team": None, "oponent_name": None}}


def test_an_answer_after_a_swap_is_for_the_side_it_was_asked_for(
        autonaApp, startgg):
    """A's sets asked for on team 1, then a swap moves A to team 2 (and
    asks again for both sides): the answer asked before the swap does not
    land on team 1, now someone else's."""
    autonaApp.board(1)
    assert WebOps.update_player(1, 1, 1, {
        "gamerTag": "A", "id": [1, 10]}) is True
    assert WebOps.update_player(1, 2, 1, {
        "gamerTag": "B", "id": [2, 20]}) is True
    autonaApp.settle()
    beforeSwap = _lastSetsAsked(startgg, 1)[-1]

    assert WebOps.swap_teams(1) == "OK"
    autonaApp.settle()
    _answerFromAWorker(beforeSwap, {"playerNumber": "1",
                                    "last_sets": [{"round_name": "A's"}]})
    autonaApp.settle()

    assert autonaApp.get("score.1.last_sets.1") in (None, {})
    # The swap asked for both sides again (deferred, once).
    assert [args for _, args, _ in startgg[-5:]] == [
        ([2, 20], [1, 10], 1386), ([2, 20], "1"), ([2, 20], "1", 1386),
        ([1, 10], "2"), ([1, 10], "2", 1386)]
