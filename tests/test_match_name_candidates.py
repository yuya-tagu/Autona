"""What the match bar's combos offer, core's (ADR-005 S4x).

The 1v1 / team boards added the loaded tournament's names to their phase /
pool / round combos themselves (_UpdatePoolCandidates /
_UpdateEntrantsMatchItem, on the provider's two signals): the real pool
names of its phases with several groups, and "Winners TOP{n}" / "Losers
TOP{n}" for its entrant count. A board built after the tournament loaded
never had them, and nothing outside the desktop could ask. Core keeps them
now (MatchNameCandidates, fed by the provider in bootstrap_core()), the
boards' combos show its lists, and ``GET /api/v1/match-names/candidates
?board=N`` answers the same lists in the same order.
"""
import pytest
from qtpy.QtWidgets import QComboBox

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


def _offered(board):
    combos = {"phase": board.phaseCombo, "pool": board.poolCombo,
              "match": board.matchCombo}
    return {kind: [combo.itemText(row) for row in range(combo.count())]
            for kind, combo in combos.items()}


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
    autonaApp.dropAllBoards()
    tournament()
    with WriteRecorder() as recorder:
        for board in (None, 1, 2, 3):
            _asked(client, board)
        autonaApp.settle()
    assert recorder.sets == []
    assert autonaApp.manager.scoreboards == {}


def test_headless_answers_the_tournament_s_names(autonaApp, client,
                                                 tournament, monkeypatch):
    _goHeadless(autonaApp, monkeypatch)
    try:
        tournament(entrants=53)
        assert _asked(client, 2)["match"][:2] == _rounds(53)
        assert _asked(client, 2)["pool"][:len(POOLS)] == POOLS
    finally:
        _dropHeadlessBoards(autonaApp)


# ── the combos ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("n", [1, 2])
def test_the_combos_offer_what_the_route_answers(autonaApp, client,
                                                 tournament, n):
    board = autonaApp.board(n)
    asked = _asked(client, n)
    assert _offered(board) == {kind: [""] + names
                               for kind, names in asked.items()}

    tournament()
    asked = _asked(client, n)
    assert asked["pool"][:len(POOLS)] == POOLS
    assert _offered(board) == {kind: [""] + names
                               for kind, names in asked.items()}

    tournament(entrants=37, phases=[])
    asked = _asked(client, n)
    assert asked["match"][:2] == _rounds(37)
    assert _offered(board) == {kind: [""] + names
                               for kind, names in asked.items()}

    # Each payload on its own is followed too (a refetch of one of them).
    _signals().tournament_data_updated.emit({"numEntrants": 41})
    autonaApp.settle()
    asked = _asked(client, n)
    assert asked["match"][:2] == _rounds(41)
    assert _offered(board) == {kind: [""] + names
                               for kind, names in asked.items()}
    _signals().tournament_phases_updated.emit(PHASES)
    autonaApp.settle()
    asked = _asked(client, n)
    assert asked["pool"][:len(POOLS)] == POOLS
    assert _offered(board) == {kind: [""] + names
                               for kind, names in asked.items()}


def test_a_board_built_after_the_tournament_offers_its_names(
        autonaApp, client, tournament):
    """The board missed the provider's signals: it reads core's lists."""
    autonaApp.dropAllBoards()
    tournament()
    board = autonaApp.board(1)
    offered = _offered(board)
    assert offered["pool"][1:1 + len(POOLS)] == POOLS
    assert offered["match"][1:3] == _rounds(300)
    assert offered == {kind: [""] + names
                       for kind, names in _asked(client, 1).items()}


def test_the_crew_board_offers_what_the_route_answers_for_it(
        autonaApp, client, tournament):
    tournament()
    board = autonaApp.board(3)
    offered = {field: [combo.itemText(row) for row in range(combo.count())]
               for field, combo in board._matchCombos.items()}
    assert offered == {kind: [""] + names
                       for kind, names in _asked(client, 3).items()}


def test_new_lists_keep_the_text_and_write_nothing(autonaApp, tournament):
    """A tournament loading under the operator's selection: the combo's
    text stays -- a name of the previous tournament included, as text
    only -- and nothing is written."""
    board = autonaApp.board(1)
    tournament()
    board.poolCombo.setCurrentIndex(board.poolCombo.findText("A2"))
    board.matchCombo.setEditText("typed by hand")
    autonaApp.settle()
    assert autonaApp.get("score.1.pool") == "A2"

    with WriteRecorder() as recorder:
        tournament(entrants=12, phases=[
            {"groups": [{"name": "C1"}, {"name": "C2"}]}])
    assert recorder.sets == []
    assert board.poolCombo.currentText() == "A2"
    assert board.poolCombo.findText("A2") < 0
    assert board.matchCombo.currentText() == "typed by hand"
    assert board.poolCombo.itemText(1) == "C1"


@pytest.fixture
def picks(autonaApp, monkeypatch):
    """Every SetMatchField the combos call, as (board, field, value); the
    operation still runs."""
    from src.core.Actions import AutonaActions
    actions = AutonaActions.instance
    original = actions.SetMatchField
    calls = []

    def spy(board, field, value):
        calls.append((board, field, value))
        return original(board, field, value)

    monkeypatch.setattr(actions, "SetMatchField", spy)
    return calls


def _winnersFinal(combo):
    name = "Winners Final"
    assert combo.findText(name) > 0, "the round terms have no Winners Final"
    return name


def test_the_blank_row_picked_after_new_lists_is_written(
        autonaApp, tournament, picks):
    """The refill of a tournament's lists left no row selected under the
    name shown (the combos have a placeholder text, so the refill's index
    is -1). The name keeps its row now, as after any render (ADR-005 S4x
    review), and the blank row picked over it is written."""
    board = autonaApp.board(1)
    combo = board.matchCombo
    name = _winnersFinal(combo)
    combo.setCurrentIndex(combo.findText(name))
    autonaApp.settle()
    assert autonaApp.get("score.1.match") == name

    with WriteRecorder() as recorder:
        tournament()
    assert recorder.sets == []
    assert _offered(board)["match"][1:3] == _rounds(300)
    assert (combo.currentText(), combo.currentIndex()) == (
        name, combo.findText(name))

    del picks[:]
    combo.setCurrentIndex(0)
    autonaApp.settle()
    assert picks == [(1, "match", "")]
    assert autonaApp.get("score.1.match") == ""


def test_a_name_the_new_lists_lack_selects_no_row(autonaApp, tournament,
                                                  picks):
    """A name the new lists do not have stays as the text alone, with no
    row selected: any pick is an index change, the blank row's included."""
    board = autonaApp.board(1)
    tournament()
    combo = board.poolCombo
    combo.setCurrentIndex(combo.findText("A2"))
    autonaApp.settle()
    tournament(entrants=12, phases=[
        {"groups": [{"name": "C1"}, {"name": "C2"}]}])
    assert (combo.currentText(), combo.currentIndex()) == ("A2", -1)

    del picks[:]
    combo.setCurrentIndex(0)
    autonaApp.settle()
    assert picks == [(1, "pool", "")]
    assert autonaApp.get("score.1.pool") == ""


@pytest.mark.parametrize("n", [1, 3])
def test_a_name_written_elsewhere_selects_its_row(autonaApp, picks, n):
    """A name the state brings (the Web, a set load): the board rendered it
    as the line edit's text over the row the index was on -- the blank row
    once picked, or where the crew board's combos start -- and picking that
    row again wrote nothing (ADR-005 S4x review). The index follows the
    text now: the name's row, or no row for a name the list lacks."""
    from src.core.Actions import AutonaActions
    board = autonaApp.board(n)
    combo = board.matchCombo
    name = _winnersFinal(combo)
    combo.setCurrentIndex(0)
    autonaApp.settle()
    assert combo.currentIndex() == 0

    for written, row in ((name, combo.findText(name)),
                         ("Typed elsewhere", -1)):
        AutonaActions.instance.SetMatchField(n, "match", written)
        autonaApp.settle()
        assert (combo.currentText(), combo.currentIndex()) == (written, row)

        del picks[:]
        combo.setCurrentIndex(0)
        autonaApp.settle()
        assert picks == [(n, "match", "")]
        assert autonaApp.get(f"score.{n}.match") == ""


def test_an_unchanged_list_leaves_the_combo_alone(autonaApp, tournament):
    board = autonaApp.board(1)
    tournament()
    combo = board.poolCombo
    combo.setEditText("half")
    combo.lineEdit().setCursorPosition(2)
    cleared = []
    combo.model().modelReset.connect(lambda: cleared.append(1))
    combo.model().rowsRemoved.connect(lambda *args: cleared.append(1))
    tournament()
    assert cleared == []
    assert combo.currentText() == "half"
    assert isinstance(combo, QComboBox)
