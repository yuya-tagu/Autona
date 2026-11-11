"""What the match bar's combos offer, core's (ADR-005 S4x).

The 1v1 / team boards added the loaded tournament's names to their phase /
pool / round combos themselves (_UpdatePoolCandidates /
_UpdateEntrantsMatchItem, on the provider's two signals): the real pool
names of its phases with several groups, and "Winners TOP{n}" / "Losers
TOP{n}" for its entrant count. A board built after the tournament loaded
never had them, and nothing outside the desktop could ask. Core keeps them
now (MatchNameCandidates, fed by the provider in bootstrap_core()), and
``GET /api/v1/match-names/candidates?board=N`` answers its lists. (The Qt
boards' combos showed the same lists until ADR-005 S4ac-3; their tests
went with them.)
"""
import pytest

from src.AutonaTournamentDataProvider import AutonaTournamentDataProvider
from src.core.MatchNameCandidates import MatchNameCandidates
from src.Helpers.AutonaLocaleHelper import AutonaLocaleHelper
from tests.fixtures.write_recording import WriteRecorder
from tests.test_api_v1 import _assertRejected, client, webApp  # noqa: F401
from tests.test_api_v1_score import _ok
from tests.test_web_operations import _dropHeadlessBoards, _goHeadless

URL = "/api/v1/match-names/candidates"

PHASES = [
    {"name": "Pools", "groups": [{"name": "A1"}, {"name": "A2"},
                                 {"name": "A3"}]},
    # One group: its name says nothing.
    {"name": "Top 8", "groups": [{"name": "1"}]},
    {"name": "Second pools", "groups": [{"name": "B1"}, {"name": "A2"},
                                        {"name": ""}, {"id": 9},
                                        {"name": 5}]},
]
POOLS = ["A1", "A2", "A3", "B1"]


def _signals():
    return AutonaTournamentDataProvider.instance.signals


@pytest.fixture
def tournament(autonaApp):
    """A tournament's two payloads, as the provider emits them; unloaded
    afterwards (the provider's empty payloads)."""
    def load(entrants=300, phases=PHASES):
        _signals().tournament_data_updated.emit({"numEntrants": entrants})
        _signals().tournament_phases_updated.emit(phases)
        autonaApp.settle()
    try:
        yield load
    finally:
        _signals().tournament_data_updated.emit({})
        _signals().tournament_phases_updated.emit([])
        autonaApp.settle()


def _generic():
    return {"phase": AutonaLocaleHelper.GetPhaseNameCandidates(),
            "pool": AutonaLocaleHelper.GetPoolNameCandidates(),
            "match": AutonaLocaleHelper.GetMatchNameCandidates()}


def _rounds(entrants):
    # An entrant count the ladder of round names does not have (it has
    # "Winners TOP64", "Losers TOP48" ...: those stay where it has them).
    terms = AutonaLocaleHelper.matchNames
    return [terms.get("winners_top_n", "Winners TOP{0}").format(entrants),
            terms.get("losers_top_n", "Losers TOP{0}").format(entrants)]


def _asked(client, board=None):
    url = URL if board is None else f"{URL}?board={board}"
    return _ok(client.get(url))["data"]


# ── core ─────────────────────────────────────────────────────────────────

def test_with_no_tournament_every_board_offers_the_terms(autonaApp):
    generic = _generic()
    for board in (None, 1, 2, 3):
        assert MatchNameCandidates.instance.Candidates(board) == generic


def test_boards_1_and_2_put_the_tournament_s_names_ahead(autonaApp,
                                                         tournament):
    tournament()
    generic = _generic()
    for board in (1, 2):
        candidates = MatchNameCandidates.instance.Candidates(board)
        assert candidates["phase"] == generic["phase"]
        assert candidates["pool"] == POOLS + generic["pool"]
        assert candidates["match"] == _rounds(300) + generic["match"]
    # The crew board's combos offer the terms alone, as they always did.
    assert MatchNameCandidates.instance.Candidates(3) == generic
    assert MatchNameCandidates.instance.Candidates(None) == generic


def test_an_unloaded_tournament_takes_its_names_away(autonaApp, tournament):
    tournament()
    _signals().tournament_data_updated.emit({})
    _signals().tournament_phases_updated.emit([])
    assert MatchNameCandidates.instance.Candidates(1) == _generic()


def test_a_name_the_terms_offer_stays_where_the_terms_have_it(autonaApp):
    candidates = MatchNameCandidates(
        generic=lambda: {"phase": ["P"], "pool": ["GA", "GB"],
                         "match": ["Winners TOP8", "GF"]},
        matchNames=lambda: {})
    candidates.FollowPhases([{"groups": [{"name": "GB"}, {"name": "X"}]}])
    candidates.FollowTournamentData({"numEntrants": 8})
    assert candidates.Candidates(1) == {
        "phase": ["P"], "pool": ["X", "GA", "GB"],
        "match": ["Losers TOP8", "Winners TOP8", "GF"]}


@pytest.mark.parametrize("payload", [
    None, [], "300", {"numEntrants": "x"}, {"numEntrants": None},
    {"numEntrants": -5}, {}])
def test_a_payload_with_no_entrant_count_offers_no_entrant_round(
        autonaApp, payload):
    candidates = MatchNameCandidates(
        generic=lambda: {"match": ["GF"]}, matchNames=lambda: {})
    candidates.FollowTournamentData({"numEntrants": 16})
    candidates.FollowTournamentData(payload)
    expected = ["Winners TOP16", "Losers TOP16", "GF"] \
        if not isinstance(payload, dict) else ["GF"]
    assert candidates.Candidates(2)["match"] == expected


@pytest.mark.parametrize("template", [5, "W{1}", "W{x}", "W{0}{"])
def test_a_round_term_that_cannot_be_formatted_is_left_out(
        autonaApp, template):
    candidates = MatchNameCandidates(
        generic=lambda: {"match": ["GF"]},
        matchNames=lambda: {"winners_top_n": template})
    candidates.FollowTournamentData({"numEntrants": 16})
    match = candidates.Candidates(1)["match"]
    assert match[-1] == "GF" and "Losers TOP16" in match
    assert all(isinstance(name, str) for name in match)


def test_terms_that_break_offer_empty_lists(autonaApp):
    def broken():
        raise ValueError("a term .format() rejects")
    candidates = MatchNameCandidates(generic=broken, matchNames=lambda: {})
    assert candidates.Candidates(1) == {"phase": [], "pool": [], "match": []}


# ── the route ────────────────────────────────────────────────────────────

def test_the_route_answers_core_s_lists_by_board(autonaApp, client,
                                                 tournament):
    tournament()
    for board in (1, 2, 3):
        assert _asked(client, board) \
            == MatchNameCandidates.instance.Candidates(board)
    assert _asked(client) == _generic()
    assert _asked(client, 1)["pool"][:len(POOLS)] == POOLS


@pytest.mark.parametrize("board", [
    "0", "4", "x", "", "1.0",
    # What int() takes and the board's number is not written as (ADR-005
    # S4x review).
    " 1", "1 ", "+1", "１", "01", "-0"])
def test_a_board_that_is_not_one_is_refused(autonaApp, client, board):
    _assertRejected(client.get(URL, query_string={"board": board}))


def test_the_route_builds_no_board_and_writes_nothing(autonaApp, client,
                                                      tournament):
    from src.core.Actions import AutonaActions

    autonaApp.dropAllBoards()
    tournament()
    with WriteRecorder() as recorder:
        for board in (None, 1, 2, 3):
            _asked(client, board)
        autonaApp.settle()
    assert recorder.sets == []
    assert not any(AutonaActions.instance.BoardEnsured(number)
                   for number in AutonaActions.BOARDS)


def test_headless_answers_the_tournament_s_names(autonaApp, client,
                                                 tournament, monkeypatch):
    _goHeadless(autonaApp, monkeypatch)
    try:
        tournament(entrants=53)
        assert _asked(client, 2)["match"][:2] == _rounds(53)
        assert _asked(client, 2)["pool"][:len(POOLS)] == POOLS
    finally:
        _dropHeadlessBoards(autonaApp)


# ── following the provider ───────────────────────────────────────────────

def test_the_route_follows_each_payload(autonaApp, client, tournament):
    """Both payloads, then each on its own (a refetch of one of them)."""
    tournament()
    assert _asked(client, 1)["pool"][:len(POOLS)] == POOLS

    tournament(entrants=37, phases=[])
    asked = _asked(client, 1)
    assert asked["match"][:2] == _rounds(37)
    assert asked["pool"] == _generic()["pool"]

    _signals().tournament_data_updated.emit({"numEntrants": 41})
    autonaApp.settle()
    assert _asked(client, 1)["match"][:2] == _rounds(41)
    _signals().tournament_phases_updated.emit(PHASES)
    autonaApp.settle()
    asked = _asked(client, 2)
    assert asked["pool"][:len(POOLS)] == POOLS
    assert asked["match"][:2] == _rounds(41)


def test_a_board_made_after_the_tournament_is_offered_its_names(
        autonaApp, client, tournament):
    """The lists are core's, not a board's: a board made after the
    provider's signals (the Qt board built late missed them until S4x) is
    answered the tournament's names."""
    autonaApp.dropAllBoards()
    tournament()
    autonaApp.ready(1)
    asked = _asked(client, 1)
    assert asked["pool"][:len(POOLS)] == POOLS
    assert asked["match"][:2] == _rounds(300)


def test_new_lists_keep_the_text_and_write_nothing(autonaApp, tournament):
    """A tournament loading under the operator's names: the state keeps
    them -- a name of the previous tournament included -- and nothing is
    written."""
    from src.core.Actions import AutonaActions

    autonaApp.ready(1)
    tournament()
    AutonaActions.instance.SetMatchField(1, "pool", "A2")
    AutonaActions.instance.SetMatchField(1, "match", "typed by hand")
    autonaApp.settle()

    with WriteRecorder() as recorder:
        tournament(entrants=12, phases=[
            {"groups": [{"name": "C1"}, {"name": "C2"}]}])
    assert recorder.sets == []
    assert autonaApp.get("score.1.pool") == "A2"
    assert autonaApp.get("score.1.match") == "typed by hand"
    assert "A2" not in MatchNameCandidates.instance.Candidates(1)["pool"]
    assert MatchNameCandidates.instance.Candidates(1)["pool"][0] == "C1"

