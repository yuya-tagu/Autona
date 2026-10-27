"""The statistics, with no Qt anywhere in sight (core's StatsService).

Headless core Step 4a moved the calculations and the writes out of
`AutonaStatsUtil`; ADR-005 S3e-5 moved the rest -- when a statistic is asked
for, the fetch (through an injected fetcher), the late answers. This module
pins core on its own, with a fake fetcher that records the requests and
answers when told: the arithmetic and the published shapes, the roster size
read from the state, the requests the triggers make, and which answers are
written. `tests/test_stats_util.py` drives the routes and the Qt transport;
`tests/test_stats_flow.py` the whole app against the recording of the tree
before the move.
"""
import os
import tempfile
import threading
from datetime import datetime
from pathlib import Path

import pytest

from src.core.PlayerService import PlayerService, SlotChange
from src.core.StateManager import StateManager as SM
from src.core.StatsService import StatsService
from tests.fixtures.stats.spies import FakeFetcher

_WORK_DIR = tempfile.mkdtemp(prefix="autona_stats_test_")
_ORIGINAL_CWD = os.getcwd()

# Board numbers no other module builds, so nothing here can be read as (or
# disturbed by) a real scoreboard's subtree. Never registered as held: the
# hold is the board widget's doing, and these tests are about the writes.
BOARD = 7
PREFIX = f"score.{BOARD}"
OTHER = 8


def _initialize_state_manager():
    # Same dance as tests/test_state_manager_held.py: Initialize() creates
    # ./out relative to the CWD, and is a process-wide no-op after its first
    # call, so the directories are made unconditionally.
    os.chdir(_WORK_DIR)
    os.makedirs(Path(_WORK_DIR) / "out", exist_ok=True)
    os.makedirs(Path(_WORK_DIR) / "user_data", exist_ok=True)
    try:
        SM.Initialize()
    finally:
        os.chdir(_ORIGINAL_CWD)


_initialize_state_manager()


@pytest.fixture(autouse=True)
def _in_work_dir(monkeypatch):
    os.chdir(_WORK_DIR)
    # No fetcher unless a test installs one, and request numbers / queued
    # refreshes of this module's own.
    monkeypatch.setattr(StatsService, "fetcher", None)
    monkeypatch.setattr(StatsService, "_generations", {})
    monkeypatch.setattr(StatsService, "_queuedTeams", {})
    yield
    # Unset first, then join: the Unset is itself a save cycle, and a still
    # running export thread resolves "./out" against whatever the CWD is by
    # the time it writes -- which must never be the project root.
    SM.Unset(PREFIX)
    SM.Unset(f"score.{OTHER}")
    if SM._export_thread is not None:
        SM._export_thread.join()
    os.chdir(_ORIGINAL_CWD)


@pytest.fixture
def fetcher(monkeypatch):
    fake = FakeFetcher()
    monkeypatch.setattr(StatsService, "fetcher", fake)
    return fake


def _roster(players=1, board=BOARD):
    """`players` slots on each team of the board -- the roster size the
    statistics read (the slots already there are kept)."""
    for team in (1, 2):
        for slot in range(1, players + 1):
            key = f"score.{board}.team.{team}.player.{slot}"
            if SM.Get(key) is None:
                SM.Set(key, {"name": ""})


def _service(players=1, board=BOARD):
    _roster(players, board)
    return StatsService(board)


# ── the roster size is the state's ───────────────────────────────────────

def test_the_roster_size_is_team_ones_slots():
    assert _service(players=1).IsSingles()
    assert not _service(players=2).IsSingles()
    assert StatsService(BOARD).PlayersPerTeam() == 2


def test_a_board_with_no_slots_has_the_default_roster():
    """S3e decision 1: no slots in the state -> DefaultPlayersPerTeam(),
    the loaded game's players_per_team (1 without a game)."""
    previous = SM.Get("game.defaults")
    try:
        SM.Unset("game.defaults")
        assert StatsService(BOARD).PlayersPerTeam() == 1
        SM.Set("game.defaults", {"players_per_team": 2})
        assert StatsService(BOARD).PlayersPerTeam() == 2
        assert not StatsService(BOARD).IsSingles()
    finally:
        if previous is None:
            SM.Unset("game.defaults")
        else:
            SM.Set("game.defaults", previous)


# ── upset factor ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("bracket, seed, expected", [
    ("DOUBLE_ELIMINATION", 1, 0),      # top seed: no math at all
    ("DOUBLE_ELIMINATION", 5, 4),      # floor(log2 4) + ceil(log2 10/3)
    ("SINGLE_ELIMINATION", 5, 2),
    ("ROUND_ROBIN", 5, 0),             # unknown format: no upset scale
])
def test_placement_math(bracket, seed, expected):
    assert StatsService.CalculatePlacementMath(bracket, seed) == expected


def test_upset_factor_is_the_distance_between_the_two_seeds():
    service = _service()
    SM.Set("score.bracket_type", "DOUBLE_ELIMINATION")
    SM.Set(f"{PREFIX}.team.1.player.1.seed", 1)
    SM.Set(f"{PREFIX}.team.2.player.1.seed", 5)

    service.UpdateUpsetFactor()

    assert SM.Get(f"{PREFIX}.upset_factor") == 4


def test_a_missing_seed_resets_the_upset_factor():
    """The previous pairing's number must not survive a player change."""
    service = _service()
    SM.Set(f"{PREFIX}.team.1.player.1.seed", 1)
    SM.Set(f"{PREFIX}.team.2.player.1.seed", 5)
    service.UpdateUpsetFactor()
    assert SM.Get(f"{PREFIX}.upset_factor") == 4

    SM.Set(f"{PREFIX}.team.2.player.1.seed", None)
    service.UpdateUpsetFactor()

    assert SM.Get(f"{PREFIX}.upset_factor") == 0


def test_a_team_board_has_no_upset_factor():
    """Seeds are per player; "the seed distance" is meaningless for 2v2."""
    SM.Set(f"{PREFIX}.team.1.player.1.seed", 1)
    SM.Set(f"{PREFIX}.team.2.player.1.seed", 5)

    _service(players=2).UpdateUpsetFactor()

    assert SM.Get(f"{PREFIX}.upset_factor") == 0


# ── head-to-head request ─────────────────────────────────────────────────

def _seedPairing(p1="p1", p2="p2", videogame=1386):
    SM.Set(f"{PREFIX}.team.1.player.1.id", p1)
    SM.Set(f"{PREFIX}.team.2.player.1.id", p2)
    SM.Set("provider_videogame.id", videogame)


def test_a_full_pairing_produces_a_request():
    _seedPairing()
    assert _service().RecentSetsRequest() == ("p1", "p2", 1386)


@pytest.mark.parametrize("p1, p2, videogame", [
    (None, "p2", 1386),      # no player 1
    ("p1", None, 1386),      # no player 2
    ("p1", "p2", None),      # no game loaded
    ("p1", "p1", 1386),      # the same player on both sides
])
def test_an_incomplete_pairing_produces_no_request(p1, p2, videogame):
    _seedPairing(p1, p2, videogame)
    assert _service().RecentSetsRequest() is None


def test_ids_are_compared_by_value_not_identity():
    """Provider ids arrive as lists as often as scalars."""
    _seedPairing(["a", 1], ["a", 1])
    assert _service().RecentSetsRequest() is None

    _seedPairing(["a", 1], ["a", 2])
    assert _service().RecentSetsRequest() is not None


def test_a_team_board_never_fetches_head_to_head():
    _seedPairing()
    assert _service(players=2).RecentSetsRequest() is None


def test_recent_sets_markers():
    service = _service()
    service.MarkRecentSetsLoading()
    assert SM.Get(f"{PREFIX}.recent_sets") == {"state": "loading", "sets": []}

    service.ClearRecentSets()
    assert SM.Get(f"{PREFIX}.recent_sets") == {"state": "done", "sets": []}


def test_an_older_head_to_head_answer_is_not_published_over_a_newer_one():
    """The request time the provider's answer carries: the older guard."""
    service = _service()
    service.UpdateRecentSets({"request_time": 200, "sets": ["new"]})
    service.UpdateRecentSets({"request_time": 100, "sets": ["old"]})

    assert SM.Get(f"{PREFIX}.recent_sets.sets") == ["new"]


# ── last sets / placement history ────────────────────────────────────────

def test_last_sets_are_published_one_key_per_set():
    _service().UpdateLastSets({
        "playerNumber": "1",
        "last_sets": [
            {"round_name": "Winners Final", "player1_score": 2,
             "player2_score": 1, "player2_name": "OPP"},
        ],
    })

    published = SM.Get(f"{PREFIX}.last_sets.1.1")
    assert published["round_name"] == "Winners Final"
    assert published["player_score"] == 2
    # The opponent half is renamed on the way in (and misspelled in the
    # published contract -- overlays read "oponent_*", so it stays).
    assert published["oponent_score"] == 1
    assert published["oponent_name"] == "OPP"


def test_a_shorter_history_does_not_leave_the_previous_players_sets():
    """Keys are sequential 1..N, so a shorter list must reset first."""
    service = _service()
    service.UpdateLastSets({
        "playerNumber": "1",
        "last_sets": [{"round_name": "A"}, {"round_name": "B"}],
    })
    assert SM.Get(f"{PREFIX}.last_sets.1.2") is not None

    service.UpdateLastSets({
        "playerNumber": "1", "last_sets": [{"round_name": "C"}]})

    assert SM.Get(f"{PREFIX}.last_sets.1.1")["round_name"] == "C"
    assert SM.Get(f"{PREFIX}.last_sets.1.2") is None


def test_clearing_a_players_stats_empties_only_that_side():
    service = _service()
    service.UpdateLastSets({"playerNumber": "1", "last_sets": [{"a": 1}]})
    service.UpdateLastSets({"playerNumber": "2", "last_sets": [{"a": 2}]})

    service.ClearLastSets(1)

    assert SM.Get(f"{PREFIX}.last_sets.1") == {}
    assert SM.Get(f"{PREFIX}.last_sets.2.1") is not None


def test_history_sets_split_the_event_date_for_the_overlays():
    stamp = datetime(2026, 3, 14, 12, 0).timestamp()
    _service().UpdateHistorySets({
        "playerNumber": "2",
        "history_sets": [
            {"placement": 3, "event_name": "Singles", "entrants": 128,
             "tournament_name": "Autona Cup", "event_date": stamp},
        ],
    })

    published = SM.Get(f"{PREFIX}.history_sets.2.1")
    assert published["placement"] == 3
    assert published["tournament_name"] == "Autona Cup"
    # Rendered here rather than in the layout, which has no date formatting.
    assert published["event_date_day"] == "14"
    assert published["event_date_year"] == "2026"
    assert published["event_date_month"] == datetime(
        2026, 3, 14).strftime("%B")


def test_a_batch_of_sets_is_one_save_cycle():
    """Ten sets must not be ten deltas on the wire."""
    seen = []
    service = _service()

    def record(payload):
        seen.append(payload)

    SM.observers.state_updated.subscribe(record)
    try:
        service.UpdateLastSets({
            "playerNumber": "1",
            "last_sets": [{"round_name": str(i)} for i in range(10)],
        })
    finally:
        SM.observers.state_updated.unsubscribe(record)

    assert len(seen) == 1, f"{len(seen)} save cycles for one fetch result"


# ── what is asked for (ADR-005 S3e-5) ────────────────────────────────────

def _lastSets(team, *rounds):
    return {"playerNumber": str(team),
            "last_sets": [{"round_name": name} for name in rounds]}


def _rounds(team):
    sets = SM.Get(f"{PREFIX}.last_sets.{team}") or {}
    return [value["round_name"] for value in sets.values()]


def test_refresh_all_asks_in_the_stats_panels_order(fetcher):
    _seedPairing([1, 10], [2, 20])
    SM.Set(f"{PREFIX}.team.1.player.1.seed", 1)
    SM.Set(f"{PREFIX}.team.2.player.1.seed", 5)

    _service().RefreshAll()

    assert fetcher.asked() == [
        ("recent_sets", ([1, 10], [2, 20], 1386)),
        ("last_sets", ([1, 10], 1)),
        ("last_sets", ([2, 20], 2)),
        ("history_sets", ([1, 10], 1, 1386)),
        ("history_sets", ([2, 20], 2, 1386)),
    ]
    assert SM.Get(f"{PREFIX}.recent_sets") == {"state": "loading", "sets": []}
    assert SM.Get(f"{PREFIX}.upset_factor") == 4


def test_another_player_asks_for_the_head_to_head_and_that_teams_sets(
        fetcher):
    _seedPairing([1, 10], [2, 20])

    _service().PlayerChanged(2, SlotChange(idChanged=True))

    assert fetcher.asked() == [
        ("recent_sets", ([1, 10], [2, 20], 1386)),
        ("last_sets", ([2, 20], 2)),
        ("history_sets", ([2, 20], 2, 1386)),
    ]


def test_another_seed_recalculates_the_upset_factor_only(fetcher):
    _seedPairing([1, 10], [2, 20])
    SM.Set(f"{PREFIX}.team.1.player.1.seed", 1)
    SM.Set(f"{PREFIX}.team.2.player.1.seed", 5)

    _service().PlayerChanged(1, SlotChange(seedChanged=True))

    assert fetcher.asked() == []
    assert SM.Get(f"{PREFIX}.upset_factor") == 4


def test_without_start_gg_the_head_to_head_is_done_and_no_history_asked(
        fetcher):
    """The last sets ask any loaded provider; the other two are Start.gg's
    (the provider's own guard, as before)."""
    fetcher.startGG = False
    _seedPairing([1, 10], [2, 20])
    service = _service()

    service.Refresh(StatsService.RECENT_SETS)
    service.Refresh(StatsService.HISTORY_SETS, 1)
    service.Refresh(StatsService.LAST_SETS, 1)

    assert fetcher.asked() == [("last_sets", ([1, 10], 1))]
    assert SM.Get(f"{PREFIX}.recent_sets") == {"state": "done", "sets": []}
    assert SM.Get(f"{PREFIX}.history_sets") is None


def test_a_team_board_fetches_nothing(fetcher):
    _seedPairing([1, 10], [2, 20])

    _service(players=2).RefreshAll()

    assert fetcher.asked() == []
    assert SM.Get(f"{PREFIX}.recent_sets") == {"state": "done", "sets": []}
    assert SM.Get(f"{PREFIX}.upset_factor") == 0


def test_a_slot_with_no_player_is_cleared_not_fetched(fetcher):
    _seedPairing(None, [2, 20])
    service = _service()
    service.UpdateLastSets(_lastSets(1, "OLD"))
    service.UpdateHistorySets({"playerNumber": "1", "history_sets": []})

    service.Refresh(StatsService.LAST_SETS, 1)
    service.Refresh(StatsService.HISTORY_SETS, 1)

    assert fetcher.asked() == []
    assert SM.Get(f"{PREFIX}.last_sets.1") == {}
    assert SM.Get(f"{PREFIX}.history_sets.1") == {}


# ── which answers are written ────────────────────────────────────────────

def test_an_answer_for_a_superseded_request_is_dropped(fetcher):
    """Two fetches of one statistic in flight: whichever answers last, only
    the latest request's answer is written."""
    _seedPairing([1, 10], [2, 20])
    service = _service()
    service.Refresh(StatsService.LAST_SETS, 1)
    service.Refresh(StatsService.LAST_SETS, 1)

    fetcher.answer(1, _lastSets(1, "NEW"))
    fetcher.answer(0, _lastSets(1, "OLD"))

    assert _rounds(1) == ["NEW"]


def test_an_answer_for_a_player_no_longer_in_the_slot_is_dropped(fetcher):
    """However the slot changed hands -- even by a write that asked for
    nothing -- the previous player's sets do not land on the next one."""
    _seedPairing([1, 10], [2, 20])
    service = _service()
    service.Refresh(StatsService.LAST_SETS, 1)
    service.Refresh(StatsService.RECENT_SETS)

    SM.Set(f"{PREFIX}.team.1.player.1.id", [3, 30])
    fetcher.answer(0, _lastSets(1, "STALE"))
    fetcher.answer(1, {"request_time": 5, "sets": ["STALE"]})

    assert _rounds(1) == []
    assert SM.Get(f"{PREFIX}.recent_sets") == {"state": "loading", "sets": []}


def test_a_cleared_slot_supersedes_the_fetch_in_flight(fetcher):
    _seedPairing([1, 10], [2, 20])
    service = _service()
    service.Refresh(StatsService.LAST_SETS, 1)

    SM.Set(f"{PREFIX}.team.1.player.1.id", None)
    service.Refresh(StatsService.LAST_SETS, 1)
    # The same player back before the first answer: still not the request
    # the slot's statistic is waiting for.
    SM.Set(f"{PREFIX}.team.1.player.1.id", [1, 10])
    fetcher.answer(0, _lastSets(1, "STALE"))

    assert SM.Get(f"{PREFIX}.last_sets.1") == {}


def test_the_head_to_head_needs_both_players_still_there(fetcher):
    _seedPairing([1, 10], [2, 20])
    service = _service()
    service.Refresh(StatsService.RECENT_SETS)

    fetcher.answer(0, {"request_time": 5, "sets": ["H2H"]})

    assert SM.Get(f"{PREFIX}.recent_sets") == {
        "state": "done", "sets": ["H2H"], "request_time": 5}


def test_a_request_on_a_team_board_supersedes_the_singles_one(fetcher):
    _seedPairing([1, 10], [2, 20])
    _service().Refresh(StatsService.LAST_SETS, 1)

    _service(players=2).Refresh(StatsService.LAST_SETS, 1)
    fetcher.answer(0, _lastSets(1, "STALE"))

    assert _rounds(1) == []


def test_a_request_without_start_gg_supersedes_the_start_gg_one(fetcher):
    """The standings are Start.gg's: asked again once another provider is
    loaded, nothing is fetched -- and the answer still in flight for the
    Start.gg request is not written."""
    _seedPairing([1, 10], [2, 20])
    service = _service()
    service.Refresh(StatsService.HISTORY_SETS, 1)

    fetcher.startGG = False
    service.Refresh(StatsService.HISTORY_SETS, 1)
    fetcher.answer(0, {"playerNumber": "1", "history_sets": [{
        "placement": 1, "event_date": 0}]})

    assert len(fetcher.asked()) == 1
    assert SM.Get(f"{PREFIX}.history_sets") is None


def test_the_boards_and_teams_are_numbered_apart(fetcher):
    _seedPairing([1, 10], [2, 20])
    SM.Set(f"score.{OTHER}.team.1.player.1.id", [1, 10])
    one = _service()
    other = _service(board=OTHER)
    one.Refresh(StatsService.LAST_SETS, 1)
    one.Refresh(StatsService.LAST_SETS, 2)
    other.Refresh(StatsService.LAST_SETS, 1)

    fetcher.answer(0, _lastSets(1, "B7T1"))
    fetcher.answer(1, _lastSets(2, "B7T2"))
    fetcher.answer(2, _lastSets(1, "B8T1"))

    assert _rounds(1) == ["B7T1"]
    assert _rounds(2) == ["B7T2"]
    assert SM.Get(f"score.{OTHER}.last_sets.1.1.round_name") == "B8T1"


def _lockProbes(fetcher):
    """Make each fetch record whether another thread could take
    StateManager.lock as it started; returns the records."""
    free = []

    def probe(*_args):
        result = []
        thread = threading.Thread(target=lambda: result.append(
            SM.lock.acquire(blocking=False) and (SM.lock.release() or True)))
        thread.start()
        thread.join()
        free.append(result[0])

    fetcher.RecentSets = probe
    fetcher.LastSets = probe
    fetcher.HistorySets = probe
    return free


def test_no_fetch_is_started_with_the_state_lock_held(fetcher):
    """The fetch is the shell's (a thread pool, a network request): it is
    started after the request is numbered, outside StateManager.lock."""
    _seedPairing([1, 10], [2, 20])
    free = _lockProbes(fetcher)
    _service().RefreshAll()

    assert free == [True] * 5


def test_an_answer_in_flight_never_passes_for_a_request_made_after_a_reset(
        fetcher):
    """The numbers come from one counter: emptying the table (the harness
    does, between tests) does not make the answer to an earlier request
    the latest one's."""
    _seedPairing([1, 10], [2, 20])
    service = _service()
    service.Refresh(StatsService.LAST_SETS, 1)
    StatsService._generations.clear()
    service.Refresh(StatsService.LAST_SETS, 1)

    fetcher.answer(0, _lastSets(1, "STALE"))
    assert _rounds(1) == []
    fetcher.answer(1, _lastSets(1, "NEW"))
    assert _rounds(1) == ["NEW"]


# ── a fetch that raises (ADR-005 S3e-5 review) ───────────────────────────
#
# The provider adapter raised on the caller's thread for an id that was not
# a list (indexing it). A statistic that cannot be asked for is logged, and
# whatever asks for several goes on to the next, as when each was a Qt
# signal of its own.

@pytest.fixture
def errors():
    from loguru import logger

    messages = []
    handler = logger.add(lambda message: messages.append(str(message)),
                         level="ERROR", format="{message}")
    try:
        yield messages
    finally:
        logger.remove(handler)


def _failing(fetcher, kind, team=None):
    """Make `fetcher` raise for `kind` (of `team`), the request recorded."""
    name = {StatsService.RECENT_SETS: "RecentSets",
            StatsService.LAST_SETS: "LastSets",
            StatsService.HISTORY_SETS: "HistorySets"}[kind]
    original = getattr(fetcher, name)

    def raising(*args):
        original(*args)
        if team is None or args[1] == team:
            raise TypeError("the fetch")

    setattr(fetcher, name, raising)


def test_a_fetch_that_raises_does_not_stop_the_rest_of_refresh_all(
        fetcher, errors):
    _seedPairing([1, 10], [2, 20])
    SM.Set(f"{PREFIX}.team.1.player.1.seed", 1)
    SM.Set(f"{PREFIX}.team.2.player.1.seed", 5)
    _failing(fetcher, StatsService.LAST_SETS, 1)

    _service().RefreshAll()

    assert [kind for kind, _ in fetcher.asked()] == [
        "recent_sets", "last_sets", "last_sets", "history_sets",
        "history_sets"]
    assert SM.Get(f"{PREFIX}.upset_factor") == 4
    assert any("last_sets (team 1): asking for the statistic failed"
               in message for message in errors)


def test_another_player_goes_on_past_a_fetch_that_raises(fetcher, errors):
    _seedPairing([1, 10], [2, 20])
    SM.Set(f"{PREFIX}.team.1.player.1.seed", 1)
    SM.Set(f"{PREFIX}.team.2.player.1.seed", 5)
    _failing(fetcher, StatsService.RECENT_SETS)

    _service().PlayerChanged(
        2, SlotChange(idChanged=True, seedChanged=True))

    assert [kind for kind, _ in fetcher.asked()] == [
        "recent_sets", "last_sets", "history_sets"]
    assert SM.Get(f"{PREFIX}.upset_factor") == 4
    assert errors


def test_a_queued_team_refresh_goes_on_past_a_fetch_that_raises(
        fetcher, errors):
    _seedPairing([1, 10], [2, 20])
    _failing(fetcher, StatsService.HISTORY_SETS, 1)

    _service().TeamsChanged((1, 2))
    fetcher.runLater()

    assert [kind for kind, _ in fetcher.asked()] == [
        "recent_sets", "last_sets", "history_sets", "last_sets",
        "history_sets"]
    assert errors


def test_a_later_that_raises_leaves_nothing_queued(fetcher, errors):
    """A board left queued would never be asked for again (every later
    TeamsChanged of it waits on the refresh that was never delivered)."""
    _seedPairing([1, 10], [2, 20])
    handed = []

    def raising(fn):
        handed.append(fn)
        raise RuntimeError("no event loop")

    fetcher.Later = raising
    service = _service()

    service.TeamsChanged((1,))          # logged, not raised
    assert StatsService._queuedTeams == {}
    service.TeamsChanged((2,))

    assert len(handed) == 2
    assert any("not asked for again" in message for message in errors)


# ── a swap / move / clear: the queued refresh ────────────────────────────

def test_teams_changed_is_one_refresh_later_however_often_it_is_asked(
        fetcher):
    _seedPairing([1, 10], [2, 20])
    service = _service()

    service.TeamsChanged((2,))
    service.TeamsChanged((1,))
    service.TeamsChanged((1, 2))

    assert fetcher.asked() == []
    assert len(fetcher.later) == 1
    fetcher.runLater()

    assert fetcher.asked() == [
        ("recent_sets", ([1, 10], [2, 20], 1386)),
        ("last_sets", ([1, 10], 1)),
        ("history_sets", ([1, 10], 1, 1386)),
        ("last_sets", ([2, 20], 2)),
        ("history_sets", ([2, 20], 2, 1386)),
    ]
    # Once run, the next change queues again.
    service.TeamsChanged((1,))
    assert len(fetcher.later) == 1


def test_teams_changed_reads_the_ids_when_it_runs(fetcher):
    _seedPairing([1, 10], [2, 20])
    service = _service()

    service.TeamsChanged((1,))
    SM.Set(f"{PREFIX}.team.1.player.1.id", [3, 30])
    fetcher.runLater()

    assert ("last_sets", ([3, 30], 1)) in fetcher.asked()


def test_the_boards_queue_apart(fetcher):
    _seedPairing([1, 10], [2, 20])
    _service().TeamsChanged((1,))
    _service(board=OTHER).TeamsChanged((1,))

    assert len(fetcher.later) == 2


def test_teams_changed_without_a_fetcher_runs_now():
    """Nothing to defer to (no shell registered a fetcher): the clears
    happen at once, and nothing is fetched."""
    _seedPairing(None, [2, 20])
    service = _service()
    service.UpdateLastSets(_lastSets(1, "OLD"))

    service.TeamsChanged((1,))

    assert SM.Get(f"{PREFIX}.recent_sets") == {"state": "done", "sets": []}
    assert SM.Get(f"{PREFIX}.last_sets.1") == {}
    assert StatsService._queuedTeams == {}


# ── the slot's writes announce (PlayerService.Announce) ──────────────────

def _slot(team=1, board=BOARD):
    return PlayerService(f"score.{board}.team.{team}.player.1")


def test_a_new_id_asks_once_and_a_reasserted_one_not_at_all(fetcher):
    _seedPairing([1, 10], [2, 20])
    _roster()

    assert _slot(2).SetPlayerId([3, 30], announce=True) is True
    assert _slot(2).SetPlayerId([3, 30], announce=True) is False
    # Without announce (an operation that asks once for everything).
    _slot(1).SetPlayerId([4, 40])

    assert [kind for kind, _ in fetcher.asked()] == [
        "recent_sets", "last_sets", "history_sets"]


def test_a_seed_asks_for_the_upset_factor(fetcher):
    _roster()
    SM.Set(f"{PREFIX}.team.2.player.1.seed", 5)

    _slot(1).SetSeed(1, announce=True)
    assert SM.Get(f"{PREFIX}.upset_factor") == 4

    _slot(1).SetSeed(3)   # no announce: the factor stays
    assert SM.Get(f"{PREFIX}.upset_factor") == 4


def test_a_load_asks_for_what_it_changed_in_its_own_cycle(fetcher):
    _seedPairing([1, 10], [2, 20])
    _roster()
    cycles = []
    SM.observers.state_updated.subscribe(cycles.append)
    try:
        _slot(1).ApplyData({"gamerTag": "NEW", "id": [5, 50], "seed": 2},
                           False, True)
    finally:
        SM.observers.state_updated.unsubscribe(cycles.append)

    assert [kind for kind, _ in fetcher.asked()] == [
        "recent_sets", "last_sets", "history_sets"]
    assert len(cycles) == 1
    written = str(cycles[0]["delta"])
    assert "recent_sets" in written and "upset_factor" in written


def test_a_load_starts_its_fetches_outside_the_state_lock(fetcher):
    """ApplyData asks inside its save cycle (Announce), once it has let go
    of StateManager.lock: the fetches start there."""
    _seedPairing([1, 10], [2, 20])
    _roster()
    free = _lockProbes(fetcher)

    _slot(1).ApplyData({"gamerTag": "NEW", "id": [5, 50]}, False, True)

    assert free == [True] * 3


def test_the_editors_slot_has_no_statistics(fetcher):
    editor = PlayerService("player_editor.1")
    try:
        editor.ApplyData({"gamerTag": "X", "id": [9, 90], "seed": 4},
                         False, True)
        editor.SetPlayerId([8, 80], announce=True)
        editor.SetSeed(2, announce=True)
    finally:
        SM.Unset("player_editor")

    assert fetcher.asked() == []
    assert StatsService._generations == {}
