"""The crew battle rules, with no QApplication and no crew board.

Board 3 is the last of the scoreboards whose behaviour could only be
exercised by building the desktop widget: the team total was a sum of
QSpinBox values filtered by whether a QLineEdit had text in it, and the
whole-board publish read nine rows off the form. None of it could be
described without Qt, which is why the crew half of ``ChangeSetData`` was
left out of headless core Step 4b rather than folded into
``SetLoadService`` -- there was no way to prove the two normalisation
rulesets differ except by reading them.

Since Step 5's crew instalment the rules are ``src/core/CrewBattleService``
and this module is where they are pinned. The differences from
``SetLoadService`` that Step 4b flagged get their own section below.

Isolation follows tests/test_set_load_service.py: StateManager writes ./out
relative to the CWD, so this runs in a temp directory, and the shared
singletons are handed back untouched -- this module runs in the same
process, against the same StateManager, as the widget harness in later
modules.
"""
import os
import tempfile
from pathlib import Path

import pytest

from src.core.CrewBattleService import CrewBattleService, CrewMember, CrewTeam
from src.Helpers.AutonaDictHelper import deep_clone
from src.core.StateManager import StateManager as SM

_WORK_DIR = tempfile.mkdtemp(prefix="autona_crew_test_")
_ORIGINAL_CWD = os.getcwd()

os.makedirs(Path(_WORK_DIR) / "out", exist_ok=True)
os.makedirs(Path(_WORK_DIR) / "user_data", exist_ok=True)

BOARD = 3
PREFIX = f"score.{BOARD}"
MAX = CrewBattleService.MAX_MEMBERS
STOCK = CrewBattleService.DEFAULT_STOCK


@pytest.fixture(autouse=True)
def _isolated_state():
    os.chdir(_WORK_DIR)
    with SM.lock:
        stateSnapshot = deep_clone(SM.state)
        savedSnapshot = deep_clone(SM.lastSavedState)
        revision = SM.revision
        changedKeys = list(SM.changedKeys)
        heldPrefixes = set(SM.heldPrefixes)
        heldKeys = {prefix: list(keys) for prefix, keys in SM.heldKeys.items()}
        # A board built by the widget harness registers score.{n} as held,
        # which would swallow every write below into heldKeys and make the
        # publish counts read zero.
        SM.heldPrefixes = set()
        SM.heldKeys = {}
        SM.state.pop("score", None)
        SM.lastSavedState.pop("score", None)
        SM.changedKeys = []
    try:
        yield
    finally:
        if SM._export_thread is not None:
            SM._export_thread.join()
        with SM.lock:
            SM.state = stateSnapshot
            SM.lastSavedState = savedSnapshot
            SM.revision = revision
            SM.changedKeys = changedKeys
            SM.heldPrefixes = heldPrefixes
            SM.heldKeys = heldKeys
        os.chdir(_ORIGINAL_CWD)


@pytest.fixture
def crew():
    return CrewBattleService(BOARD)


class _Publishes:
    """Counts the save cycles that actually reached the delta stream.

    score.3 is not a held subtree, so every write publishes -- which makes
    "one operation, one broadcast" directly observable here, unlike on the
    1v1 / team boards where it has to be counted in held notifications.
    """

    def __init__(self):
        self.deltas = []
        SM.observers.state_updated.subscribe(self._onDelta)

    def _onDelta(self, payload):
        self.deltas.append(payload)

    def close(self):
        SM.observers.state_updated.unsubscribe(self._onDelta)


@pytest.fixture
def publishes():
    spy = _Publishes()
    try:
        yield spy
    finally:
        spy.close()


def _fullRoster(names=(), stock=STOCK, active=0):
    """A nine-row crew; `names` fills the first rows, the rest stay blank."""
    return tuple(
        CrewMember(
            name=names[index] if index < len(names) else "",
            stock=stock,
            active=(index + 1 == active))
        for index in range(MAX))


def _seed(crew, names1=(), names2=(), memberNum=3, bestOf=0):
    return crew.SeedBoardState(
        [CrewTeam(name="", color="#fe0000", members=_fullRoster(names1)),
         CrewTeam(name="", color="#0071bc", members=_fullRoster(names2))],
        memberNum, bestOf)


# ── key paths ────────────────────────────────────────────────────────────

def test_the_service_is_bound_to_one_board(crew):
    assert crew.prefix == PREFIX
    assert crew._teamKey(1, "score") == f"{PREFIX}.team.1.score"
    assert crew._memberKey(2, 4, "stock") == f"{PREFIX}.team.2.player.4.stock"


# ── the team total ───────────────────────────────────────────────────────

def test_the_total_sums_the_named_visible_members(crew):
    _seed(crew, names1=("A", "B", "C"))

    assert SM.Get(f"{PREFIX}.team.1.score") == 3 * STOCK


def test_an_unnamed_row_contributes_nothing(crew):
    """Every row starts at full stocks, so this is what keeps a fresh board
    at 0 instead of showing nine."""
    _seed(crew)

    assert SM.Get(f"{PREFIX}.team.1.score") == 0


def test_a_whitespace_name_is_not_a_name(crew):
    _seed(crew, names1=("   ", "B"))

    assert crew.TeamTotal(1) == STOCK


def test_rows_past_the_member_count_are_not_in_this_crew_battle(crew):
    _seed(crew, names1=tuple(f"P{index}" for index in range(1, MAX + 1)),
          memberNum=3)

    assert crew.TeamTotal(1) == 3 * STOCK


def test_a_missing_member_count_means_an_empty_crew(crew):
    """TeamTotal is called on whatever the state holds, including nothing.

    A board that has not been seeded (the placeholder
    AutonaScoreboardManager writes for an unbuilt board 3) must produce 0
    rather than raise.
    """
    assert crew.TeamTotal(1) == 0


def test_a_non_numeric_stock_does_not_take_the_total_down_with_it(crew):
    _seed(crew, names1=("A", "B"))
    SM.Set(f"{PREFIX}.team.1.player.1.stock", "not a number")

    assert crew.TeamTotal(1) == STOCK


# ── the roster ───────────────────────────────────────────────────────────

def test_publishing_stocks_writes_every_row_and_the_total(crew, publishes):
    _seed(crew, names1=("A", "B", "C"))
    publishes.deltas.clear()

    total = crew.PublishStocks(1, [1, 2, 3] + [STOCK] * (MAX - 3))

    assert total == 6
    assert SM.Get(f"{PREFIX}.team.1.player.1.stock") == 1
    assert SM.Get(f"{PREFIX}.team.1.player.9.stock") == STOCK
    assert SM.Get(f"{PREFIX}.team.1.score") == 6
    assert len(publishes.deltas) == 1, (
        "the stock column and the total it produces must be one broadcast")


def test_naming_a_slot_recomputes_the_total_in_the_same_cycle(
        crew, publishes):
    """The pairing that used to be the caller's to remember.

    Naming a slot is exactly what decides whether its stocks count, so a
    publish carrying the new name with the old total is a board that
    contradicts itself.
    """
    _seed(crew)
    publishes.deltas.clear()

    total = crew.SetMemberName(1, 1, "Alpha", [STOCK] * MAX)

    assert total == STOCK
    assert SM.Get(f"{PREFIX}.team.1.player.1.name") == "Alpha"
    assert SM.Get(f"{PREFIX}.team.1.score") == STOCK
    assert len(publishes.deltas) == 1


def test_clearing_a_slot_name_takes_its_stocks_off_the_total(crew):
    _seed(crew, names1=("Alpha",))

    assert crew.SetMemberName(1, 1, None, [STOCK] * MAX) == 0
    assert SM.Get(f"{PREFIX}.team.1.player.1.name") == ""


def test_the_member_count_is_written_for_both_crews(crew, publishes):
    _seed(crew, names1=("A", "B", "C", "D", "E"),
          names2=("F", "G", "H", "I", "J"), memberNum=3)
    publishes.deltas.clear()

    totals = crew.SetMemberNum(5, [[STOCK] * MAX, [STOCK] * MAX])

    assert totals == [5 * STOCK, 5 * STOCK]
    assert SM.Get(f"{PREFIX}.team.1.memberNum") == 5
    assert SM.Get(f"{PREFIX}.team.2.memberNum") == 5
    assert len(publishes.deltas) == 1, (
        "raising the member count published the two crews separately")


# ── the active member marker ─────────────────────────────────────────────

def test_the_active_member_is_published_as_a_string(crew):
    crew.SetActiveMember(1, 2)

    # "2", not 2: what the crew overlay has always read.
    assert SM.Get(f"{PREFIX}.team.1.activeMember") == "2"


def test_nobody_playing_is_published_as_zero(crew):
    crew.SetActiveMember(1, 0)

    assert SM.Get(f"{PREFIX}.team.1.activeMember") == "0"


def test_an_unparseable_active_member_falls_back_to_nobody(crew):
    crew.SetActiveMember(1, None)

    assert SM.Get(f"{PREFIX}.team.1.activeMember") == "0"


# ── best of ──────────────────────────────────────────────────────────────

def test_the_crew_best_of_publishes_only_the_raw_key(crew):
    """No best_of_text / first_to* -- see the module docstring.

    AutonaActions.SetBestOf derives five more keys from the localized round
    name tables. Board 3 has no consumer for them, and this path must not
    require those tables to have been loaded.
    """
    crew.SetBestOf(5)

    assert SM.Get(f"{PREFIX}.best_of") == 5
    for derived in ("best_of_text", "best_of_short_text", "first_to",
                    "first_to_short_text", "first_to_text"):
        assert SM.Get(f"{PREFIX}.{derived}") is None


@pytest.mark.parametrize("payload,expected", [
    ({"bestOf": 5}, 5),
    ({"bestOf": "5"}, 5),
    ({"bestOf": 0}, 0),                 # "no set length", not "not carried"
    ({"bestOf": None}, None),
    ({}, None),
    ({"bestOf": "three"}, None),
    (None, None),
    ("nonsense", None),
])
def test_the_set_length_a_payload_carries(payload, expected):
    assert CrewBattleService.BestOfFromSetData(payload) == expected


def test_a_broken_set_length_no_longer_abandons_the_rest_of_the_payload():
    """The old path handed int(...) straight to a QSpinBox.

    That raised inside a Qt signal handler, so the traceback went to stderr
    and everything after it in the same ChangeSetData was dropped. Parsing
    here is what makes the "tolerant by design" comment on that method true.
    """
    assert CrewBattleService.BestOfFromSetData({"bestOf": []}) is None


# ── ApplyMatchInfo: the crew reading, which is NOT SetLoadService's ──────
#
# Step 4b listed these differences as the reason board 3 could not simply
# reuse SetLoadService. Each one is pinned here against the crew service and
# stated against its board-1/2 counterpart.

def test_an_empty_round_name_clears_the_crew_round(crew):
    crew.ApplyMatchInfo({"round_name": "Winners Final"})
    crew.ApplyMatchInfo({"round_name": ""})

    # SetLoadService.ApplyMatchInfo would keep "Winners Final" here: on
    # boards 1/2 an empty round name means "this payload does not carry one".
    assert SM.Get(f"{PREFIX}.match") == ""


def test_an_empty_phase_clears_the_crew_phase(crew):
    crew.ApplyMatchInfo({"tournament_phase": "Pools"})
    crew.ApplyMatchInfo({"tournament_phase": ""})

    assert SM.Get(f"{PREFIX}.phase") == ""


def test_an_absent_pool_key_leaves_the_pool_alone(crew):
    crew.ApplyMatchInfo({"tournament_pool": "A"})
    crew.ApplyMatchInfo({"tournament_phase": "Pools"})

    assert SM.Get(f"{PREFIX}.pool") == "A"


def test_an_explicit_null_pool_leaves_the_pool_alone(crew):
    """The one place crew is *less* eager than boards 1/2.

    SetLoadService tests `"tournament_pool" in data`, so a payload carrying
    an explicit null clears the pool there. Crew tests the value, so it does
    not.
    """
    crew.ApplyMatchInfo({"tournament_pool": "A"})
    crew.ApplyMatchInfo({"tournament_pool": None})

    assert SM.Get(f"{PREFIX}.pool") == "A"


def test_the_crew_board_never_publishes_match_en(crew):
    crew.ApplyMatchInfo(
        {"round_name": "Winners Final", "round_name_en": "Winners Final"})

    assert SM.Get(f"{PREFIX}.match_en") is None


def test_applying_the_same_payload_twice_writes_nothing_the_second_time(
        crew, publishes):
    """A followed set re-sends the same values every few seconds."""
    crew.ApplyMatchInfo({"round_name": "WF", "tournament_phase": "Top 8"})
    publishes.deltas.clear()

    assert crew.ApplyMatchInfo(
        {"round_name": "WF", "tournament_phase": "Top 8"}) is False
    assert publishes.deltas == []


def test_a_payload_with_nothing_crew_understands_is_not_an_error(crew):
    assert crew.ApplyMatchInfo({"team1score": 2}) is False
    assert crew.ApplyMatchInfo(None) is False
    assert crew.ApplyMatchInfo("nonsense") is False


# ── the whole board ──────────────────────────────────────────────────────

def test_publishing_a_board_is_one_broadcast(crew, publishes):
    _seed(crew)
    publishes.deltas.clear()

    totals = crew.PublishBoard(
        [CrewTeam(name="Alpha", color="#111111",
                  members=_fullRoster(("A1", "A2"), active=2)),
         CrewTeam(name="Bravo", color="#222222",
                  members=_fullRoster(("B1",)))],
        3)

    assert totals == [2 * STOCK, STOCK]
    assert SM.Get(f"{PREFIX}.team.1.teamName") == "Alpha"
    assert SM.Get(f"{PREFIX}.team.2.color") == "#222222"
    assert SM.Get(f"{PREFIX}.team.1.activeMember") == "2"
    assert SM.Get(f"{PREFIX}.team.2.activeMember") == "0"
    assert len(publishes.deltas) == 1, (
        "a swap published field by field would put a half-swapped board on "
        "the stream")


def test_the_launch_write_fills_everything_an_overlay_reads(crew):
    _seed(crew, memberNum=3, bestOf=0)

    assert SM.Get(f"{PREFIX}.phase") == ""
    assert SM.Get(f"{PREFIX}.match") == ""
    assert SM.Get(f"{PREFIX}.best_of") == 0
    for team in (1, 2):
        assert SM.Get(f"{PREFIX}.team.{team}.teamName") == ""
        assert SM.Get(f"{PREFIX}.team.{team}.memberNum") == 3
        assert SM.Get(f"{PREFIX}.team.{team}.activeMember") == "0"
        assert SM.Get(f"{PREFIX}.team.{team}.score") == 0
        for member in range(1, MAX + 1):
            assert SM.Get(
                f"{PREFIX}.team.{team}.player.{member}.name") == ""
            assert SM.Get(
                f"{PREFIX}.team.{team}.player.{member}.stock") == STOCK


def test_the_launch_write_does_not_seed_a_pool(crew):
    """Recorded, not fixed: adding a key changes a fresh program_state.json.

    The widget has always seeded `phase` and `match` but not `pool`, and
    boards 1/2 seed none of the three. See tests/test_crew_board.py for the
    same pin against the real board.
    """
    _seed(crew)

    assert SM.Get(f"{PREFIX}.pool") is None


def test_the_launch_write_is_one_broadcast(crew, publishes):
    _seed(crew)

    assert len(publishes.deltas) == 1


def test_a_reseed_produces_the_same_board_as_a_clear(crew):
    """The launch write and the clear-all flush are the same pass.

    They used to be two loops over the same keys, and they had already
    drifted once (the launch write set stocks twice, the flush resolved the
    active marker differently).
    """
    _seed(crew, names1=("A",), names2=("B",))
    populated = deep_clone(SM.Get(PREFIX))

    # What CommandClearAll hands over once it has blanked the rows.
    crew.PublishBoard(
        [CrewTeam(name="", color="#fe0000", members=_fullRoster()),
         CrewTeam(name="", color="#0071bc", members=_fullRoster())], 3)
    cleared = deep_clone(SM.Get(PREFIX))

    _seed(crew)
    fresh = deep_clone(SM.Get(PREFIX))

    assert cleared == fresh
    # ...and the comparison is not vacuous: a populated board differs.
    assert populated != fresh
