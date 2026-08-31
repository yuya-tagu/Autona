"""The stats calculations, with no Qt anywhere in sight.

This is what moving them out of `AutonaStatsUtil` bought (headless core
Step 4a): before, every one of these assertions needed a QApplication, a
bootstrapped singleton layer and a real scoreboard widget, because the
upset-factor math and the last-sets shaping lived on an object that existed
only to hold Qt signals. `tests/test_stats_util.py` still drives the Qt
transport end to end -- the routes, the signals, the board wiring; this
module is the arithmetic and the published shapes.

`playersPerTeam` is injected, so "1v1 only" can be exercised both ways
without building a team board.
"""
import os
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from src.core.StateManager import StateManager as SM
from src.core.StatsService import StatsService

_WORK_DIR = tempfile.mkdtemp(prefix="autona_stats_test_")
_ORIGINAL_CWD = os.getcwd()

# A board number no other module builds, so nothing here can be read as (or
# disturbed by) a real scoreboard's subtree. Never registered as held: the
# hold is the board widget's doing, and these tests are about the writes.
BOARD = 7
PREFIX = f"score.{BOARD}"


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
def _in_work_dir():
    os.chdir(_WORK_DIR)
    yield
    # Unset first, then join: the Unset is itself a save cycle, and a still
    # running export thread resolves "./out" against whatever the CWD is by
    # the time it writes -- which must never be the project root.
    SM.Unset(PREFIX)
    if SM._export_thread is not None:
        SM._export_thread.join()
    os.chdir(_ORIGINAL_CWD)


def _service(players=1):
    return StatsService(BOARD, playersPerTeam=lambda: players)


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


def test_a_stale_head_to_head_answer_is_dropped():
    """Two fetches can be in flight; the thread pool orders neither."""
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

    def record(payload):
        seen.append(payload)

    SM.observers.state_updated.subscribe(record)
    try:
        _service().UpdateLastSets({
            "playerNumber": "1",
            "last_sets": [{"round_name": str(i)} for i in range(10)],
        })
    finally:
        SM.observers.state_updated.unsubscribe(record)

    assert len(seen) == 1, f"{len(seen)} save cycles for one fetch result"
