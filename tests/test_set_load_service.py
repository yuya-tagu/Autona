"""What a loaded set means, with no QApplication and no scoreboard.

Every rule here used to be expressed as widget manipulation inside
``AutonaScoreboardWidget.ChangeSetData``: a combo's text set so its
editingFinished lambda would write the round name, the score spinboxes
driven so their valueChanged would write the score, and "which side is which
after a swap" re-derived four times from four reversed lists. None of it
could be exercised without building a board, which is why the interesting
cases -- an empty pool clearing the previous one, a phase-only web edit
leaving it alone -- lived as comments rather than tests.

Since headless core Step 4b the rules are ``src/core/SetLoadService.py`` and
this module is where they are pinned.

Isolation follows tests/test_game_tracker_service.py: StateManager writes
./out relative to the CWD, so this runs in a temp directory, and the shared
singletons are handed back untouched -- this module runs in the same
process, against the same StateManager, as the widget harness in later
modules.
"""
import os
import tempfile
from pathlib import Path

import pytest

from src.core.SetLoadService import SetLoadService, TEAM_LOGO_DIR
from src.core.SettingsManager import SettingsManager
from src.core.DictHelper import deep_clone
from src.core.StateManager import StateManager as SM

_WORK_DIR = tempfile.mkdtemp(prefix="autona_setload_test_")
_ORIGINAL_CWD = os.getcwd()

os.makedirs(Path(_WORK_DIR) / "out", exist_ok=True)
os.makedirs(Path(_WORK_DIR) / "user_data", exist_ok=True)

BOARD = 1
PREFIX = f"score.{BOARD}"


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
        # Same reason as the tracker suite: a board built by the widget
        # harness registers score.1 as held, which would swallow every write
        # below into heldKeys and make the save-cycle counts read zero.
        SM.heldPrefixes = set()
        SM.heldKeys = {}
        SM.state.pop("score", None)
        SM.lastSavedState.pop("score", None)
        SM.changedKeys = []
    settingsSnapshot = SettingsManager.settings
    SettingsManager.settings = {}
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
        SettingsManager.settings = settingsSnapshot
        os.chdir(_ORIGINAL_CWD)


class _Swap:
    """A settable stand-in for the board's teamsSwapped attribute."""

    def __init__(self, value=False):
        self.value = value

    def __call__(self):
        return self.value


@pytest.fixture
def swap():
    return _Swap(False)


@pytest.fixture
def service(swap):
    return SetLoadService(BOARD, swap)


def _score(team):
    return SM.Get(f"{PREFIX}.team.{team}.score")


# ── which side is which ──────────────────────────────────────────────────

def test_teams_map_straight_through_when_not_swapped(service):
    assert service.DisplayTeam(1) == 1
    assert service.DisplayTeam(2) == 2


def test_teams_are_mirrored_after_a_swap(service, swap):
    swap.value = True
    assert service.DisplayTeam(1) == 2
    assert service.DisplayTeam(2) == 1


# ── match information ────────────────────────────────────────────────────

def test_round_and_phase_are_written(service):
    service.ApplyMatchInfo({
        "round_name": "Winners Final",
        "round_name_en": "Winners Final",
        "tournament_phase": "Bracket",
    })
    assert SM.Get(f"{PREFIX}.match") == "Winners Final"
    assert SM.Get(f"{PREFIX}.match_en") == "Winners Final"
    assert SM.Get(f"{PREFIX}.phase") == "Bracket"


def test_an_empty_round_name_does_not_clear_the_round(service):
    """set_route builds {'round_name': match} from a query parameter."""
    service.ApplyMatchInfo({"round_name": "Grand Final"})
    service.ApplyMatchInfo({"round_name": ""})
    assert SM.Get(f"{PREFIX}.match") == "Grand Final"


def test_match_en_is_blanked_by_a_payload_that_carries_none(service):
    # Long-standing behaviour, kept deliberately: match_en is written on
    # every payload, so a web edit with no English round text clears it.
    service.ApplyMatchInfo({"round_name_en": "Winners Final"})
    service.ApplyMatchInfo({"tournament_phase": "Bracket"})
    assert SM.Get(f"{PREFIX}.match_en") == ""


def test_an_empty_pool_clears_the_previous_one(service):
    """A set from a pool-less phase carries tournament_pool = ""."""
    service.ApplyMatchInfo({"tournament_pool": "Pool A"})
    service.ApplyMatchInfo({"tournament_pool": ""})
    assert SM.Get(f"{PREFIX}.pool") == ""


def test_a_payload_without_a_pool_key_leaves_the_pool_alone(service):
    """The other half: a phase-only edit must not touch the pool."""
    service.ApplyMatchInfo({"tournament_pool": "Pool A"})
    service.ApplyMatchInfo({"tournament_phase": "Bracket"})
    assert SM.Get(f"{PREFIX}.pool") == "Pool A"


# ── scores ───────────────────────────────────────────────────────────────

def test_scores_land_on_their_own_teams(service):
    service.ApplyScores({"team1score": 3, "team2score": 1})
    assert (_score(1), _score(2)) == (3, 1)


def test_scores_follow_the_swap(service, swap):
    swap.value = True
    service.ApplyScores({"team1score": 3, "team2score": 1})
    assert (_score(1), _score(2)) == (1, 3)


def test_reset_score_zeroes_both_sides(service):
    service.ApplyScores({"team1score": 3, "team2score": 1})
    service.ApplyScores({"reset_score": True})
    assert (_score(1), _score(2)) == (0, 0)


def test_reset_still_applies_when_score_updates_are_disabled(service):
    service.ApplyScores({"team1score": 3, "team2score": 1})
    SettingsManager.settings = {"general": {"disable_scoreupdate": True}}
    service.ApplyScores({"reset_score": True, "team1score": 9, "team2score": 9})
    assert (_score(1), _score(2)) == (0, 0)


def test_disable_scoreupdate_ignores_the_payload_scores(service):
    SettingsManager.settings = {"general": {"disable_scoreupdate": True}}
    service.ApplyScores({"team1score": 3, "team2score": 1})
    assert _score(1) is None and _score(2) is None


def test_scores_are_clamped_to_the_spinbox_range(service):
    service.ApplyScores({"team1score": 500, "team2score": -4})
    assert (_score(1), _score(2)) == (99, 0)


def test_a_non_numeric_score_is_skipped_not_written(service):
    """It used to reach QSpinBox.setValue and abandon the rest of the load."""
    service.ApplyScores({"team1score": "oops", "team2score": 2})
    assert _score(1) is None
    assert _score(2) == 2


def test_a_numeric_string_score_is_accepted(service):
    service.ApplyScores({"team1score": "3"})
    assert _score(1) == 3


# ── losers flags ─────────────────────────────────────────────────────────

def test_losers_flags_follow_the_swap(service, swap):
    swap.value = True
    service.ApplyLosers({"team1losers": True, "team2losers": False})
    assert SM.Get(f"{PREFIX}.team.2.losers") is True
    assert SM.Get(f"{PREFIX}.team.1.losers") is False


def test_a_missing_losers_flag_is_left_alone(service):
    service.ApplyLosers({"team1losers": True, "team2losers": True})
    service.ApplyLosers({"team1losers": False})
    assert SM.Get(f"{PREFIX}.team.1.losers") is False
    assert SM.Get(f"{PREFIX}.team.2.losers") is True


def _named(team, tag):
    path = f"{PREFIX}.team.{team}.player.1"
    SM.Set(f"{path}.name", tag)
    SM.Set(f"{path}.team", "")
    return path


def test_a_loaded_losers_flag_takes_its_teams_names_along(service, swap):
    """The " [L]" lands on the display team the flag landed on, in the
    flag's own save cycle (ADR-005 S2b) -- the board's per-card
    _ApplyLosersToPlayers used to do this half."""
    left = _named(1, "LEFT")
    right = _named(2, "RIGHT")
    swap.value = True
    deltas = []
    SM.observers.state_updated.subscribe(deltas.append)
    try:
        assert service.ApplyLosers({"team1losers": True}) is True
    finally:
        SM.observers.state_updated.unsubscribe(deltas.append)

    assert SM.Get(f"{right}.mergedName") == "RIGHT [L]"
    assert SM.Get(f"{right}.mergedOnlyName") == "RIGHT [L]"
    assert SM.Get(f"{left}.mergedName") is None
    assert len(deltas) == 1


def test_an_unchanged_losers_flag_leaves_the_names_alone(service):
    named = _named(1, "ALPHA")
    service.ApplyLosers({"team1losers": True})
    SM.Set(f"{named}.mergedName", "kept")

    assert service.ApplyLosers({"team1losers": True}) is False

    assert SM.Get(f"{named}.mergedName") == "kept"


# ── team name and crest ──────────────────────────────────────────────────

def test_team_name_follows_the_swap(service, swap):
    swap.value = True
    service.ApplyTeamName(1, "Alpha")
    assert SM.Get(f"{PREFIX}.team.2.teamName") == "Alpha"


def test_a_team_name_with_a_crest_publishes_its_path(service):
    os.makedirs(TEAM_LOGO_DIR, exist_ok=True)
    Path(TEAM_LOGO_DIR, "alpha.png").write_bytes(b"")
    try:
        service.ApplyTeamName(1, "Alpha")
        assert SM.Get(f"{PREFIX}.team.1.logo") == f"{TEAM_LOGO_DIR}/alpha.png"
    finally:
        Path(TEAM_LOGO_DIR, "alpha.png").unlink()


def test_a_team_name_with_no_crest_file_publishes_no_logo(service):
    service.ApplyTeamName(1, "Alpha")
    assert SM.Get(f"{PREFIX}.team.1.logo") is None


def test_a_team_name_change_to_no_crest_clears_a_previous_logo(service):
    os.makedirs(TEAM_LOGO_DIR, exist_ok=True)
    Path(TEAM_LOGO_DIR, "alpha.png").write_bytes(b"")
    try:
        service.ApplyTeamName(1, "Alpha")
        assert SM.Get(f"{PREFIX}.team.1.logo") == f"{TEAM_LOGO_DIR}/alpha.png"
    finally:
        Path(TEAM_LOGO_DIR, "alpha.png").unlink()

    service.ApplyTeamName(1, "Beta")
    assert SM.Get(f"{PREFIX}.team.1.logo") is None


def test_a_missing_team_name_is_stored_as_empty(service):
    # p1_name is absent for a solo entrant; setText(None) used to raise.
    service.ApplyTeamName(1, None)
    assert SM.Get(f"{PREFIX}.team.1.teamName") == ""


# ── bracket type ─────────────────────────────────────────────────────────

def test_bracket_type_is_published_beside_the_boards(service):
    assert service.ApplyBracketType({"bracket_type": 1}) is True
    assert SM.Get("score.bracket_type") == 1


def test_a_payload_without_a_bracket_type_asks_for_no_recalculation(service):
    assert service.ApplyBracketType({"round_name": "GF"}) is False


# ── set lifecycle ────────────────────────────────────────────────────────

def test_begin_set_records_the_followed_set_in_one_cycle(service):
    before = SM.revision
    service.BeginSet("12345", "stream", "Station 3")
    assert SM.Get(f"{PREFIX}.set_id") == "12345"
    assert SM.Get(f"{PREFIX}.auto_update") == "stream"
    assert SM.Get(f"{PREFIX}.station") == "Station 3"
    assert SM.revision == before + 1


def test_clearing_the_tracking_forgets_all_three_keys(service):
    service.BeginSet("12345", "stream", "Station 3")
    service.ClearSetTracking()
    assert SM.Get(f"{PREFIX}.set_id") is None
    assert SM.Get(f"{PREFIX}.auto_update") is None
    assert SM.Get(f"{PREFIX}.station") is None


def test_the_station_queue_is_published(service):
    service.SetStationQueue([{"id": 1}])
    assert SM.Get(f"{PREFIX}.station_queue") == [{"id": 1}]


# ── stale async refreshes ────────────────────────────────────────────────

@pytest.mark.parametrize("data", [None, {}, {"id": None}, {"entrants": []}])
def test_an_empty_refresh_is_stale(data):
    assert SetLoadService.IsStaleUpdate(data, "12345") is True


def test_a_refresh_for_another_set_is_stale():
    assert SetLoadService.IsStaleUpdate({"id": "999"}, "12345") is True


def test_a_refresh_for_the_current_set_is_applied():
    assert SetLoadService.IsStaleUpdate({"id": 12345}, "12345") is False


# ── no spurious writes ───────────────────────────────────────────────────

def test_reapplying_the_same_payload_publishes_nothing(service):
    payload = {
        "round_name": "Winners Final",
        "round_name_en": "Winners Final",
        "tournament_phase": "Bracket",
        "tournament_pool": "",
        "team1score": 2,
        "team2score": 1,
        "team1losers": False,
        "team2losers": True,
    }
    for apply in (service.ApplyMatchInfo, service.ApplyScores,
                  service.ApplyLosers):
        apply(payload)
    if SM._export_thread is not None:
        SM._export_thread.join()

    before = SM.revision
    for apply in (service.ApplyMatchInfo, service.ApplyScores,
                  service.ApplyLosers):
        # Every one of them reports "nothing to do" the second time...
        assert apply(payload) is False
    # ...and no delta went out, so nothing marked the board dirty either.
    assert SM.revision == before
